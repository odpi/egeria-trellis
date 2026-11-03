/* Curate — review and commit.
 *
 * Moved out of app.js (PLAN-FINISH-REPOS.md, Part 2 §1): app.js keeps
 * routing, shared state and chrome; each stage owns its own pane's
 * rendering logic. `renderCurate` is the only export app.js's generic
 * Questions-checklist engine calls directly, when `state.stage === 'curate'`
 * (see `loadPane()`'s big shared function in app.js); everything else in
 * this file — the component-tree/branch/leaf review, the catalogue-depth
 * offer, the verdict recorder — is reached only from within `renderCurate`
 * itself or from other functions in this module, so it stays unexported.
 */
import { ago, publishedEarlierSentence, PUBLISHED_EARLIER_WORD } from '/static/next/format.js';
import { stateEntry } from '/static/next/glyphs.js';
import { openDialog, closeCellDetail } from '/static/next/worklist.js';
import {
  getComponentFinder, getBulkFacts, getCuratePlan, curateCommit, getCuration, pollActivity,
  getComponentTree, getComponentLeaves, postBranchVerdicts,
  getCatalogueDepthOffer, postCatalogueDepthOfferOutcome,
  getComponentBlueprints, postBlueprintVerdict, getLatestCuration, getAnalysisResults,
} from '/static/re-api.js';
import {
  bandFrameHtml, renderFindableBand, renderPeopleBand, databaseWorkHtml, databaseScopeHtml, databaseStructureHtml,
  filesystemWorkHtml, renderRulesBlock, renderQualityBlock, UNSIGNED_LABEL,
} from '/static/next/stages/curate-bands.js';
import { renderCatalogueScope, setCommitPanelGate, startScopeCommitIfShown } from '/static/next/stages/curate-scope.js';
import { renderPublishBand, renderPublishRepo, renderPublishDb, paintDbPublishState, showPublishFailure } from '/static/next/stages/publish.js';
import { renderPublishTable } from '/static/next/stages/publish-table.js';
import { ARCHITECTURE_CHANGED } from '/static/next/stages/architecture-publish.js';
import { repoCommitPanelHtml, commitHeaderHtml, commitResultHtml, commitVerdict, manifestCounts, publishLabel } from '/static/next/stages/repo-manifest.js';
import { createScopeController, scopeListHtml, visibleRows } from '/static/next/stages/resource-scope.js';
import { mountDependencyTable } from '/static/next/stages/dependencies.js';
import { mountDeclaredTopology } from '/static/next/stages/declared-topology.js';
import { admissionHtml, admissionNoteHtml } from '/static/next/stages/curate-admission.js';
import {
  state, esc, $, icon, tnum, factGlyph, ensureRailShowing, railClaim, railFrame,
  openMembers, fmtSeconds, tokens, mermaidForKroki, themeSvgElement, deferredAttrs,
  apiEntityType, openCurate, openContext, refreshStageRail,
} from '/static/next/app.js';
import { stageTabsOn, tabOfSection, STAGE_TAB_IDS } from '/static/next/stages/stage-tabs.js';
import { setCommitLine, commitLineFromRecord } from '/static/next/stages/curate-tabs.js';
import { contentsChosen } from '/static/next/stages/curate-counts.js';




/* ════════════════════════════════════════════════════════════════════════
 * Curate — review and commit
 *
 * One decision, one screen, one commit — and the commit has consequences in
 * two directions, so the screen's whole job is to show both before the
 * press. Three columns answer one question, "what should the catalogue know
 * about this?": what it IS, what it HOLDS, what it is MADE OF. Then how it
 * relates, then the manifest of what gets written. Almost everything on it
 * was decided earlier; this is where it is seen assembled.
 *
 * Rules held (Enrichment and Curate Wireframes; Repo Handoff item 6):
 * - Candidates with evidence, never auto-applied: every row names its
 *   analysis, its state, and opens its members. "Infrastructure Asset? 15
 *   Dockerfiles", with the question mark.
 * - Testimony is copied, measurements are linked, unresolved things travel.
 * - Only worthy things get curated: the population is `tracking` or `using`,
 *   and the pane says so rather than hiding the resource or the button.
 * - The commit is asynchronous and can fail elsewhere: the record shows each
 *   step as it lands, and a failed step is a failed step, not a lost act.
 * ═══════════════════════════════════════════════════════════════════════ */

// `pick` marks the one column whose rows are confirmed one by one; the
// others are counts whose members are reviewed. The contained set is NEVER taken whole: each folder and
// file is chosen by name in "what's in it" (brief 2a) and the press publishes that record.
/** What pressing Publish really does (workflows/curate_commit.py `execute_curation`, brief section 1):
 *  it publishes the survey ALREADY KEPT, plus the files and folders you chose. It never surveys unless you
 *  tick the box under the button, and then only the stale steps. */
export const CATALOG_SENTENCE =
  'Publishes the survey already kept on this repository, and the files and folders you chose, to Egeria. It does not run a survey: '
  + 'tick the box under the button to re-survey the stale steps first.';

const CURATE_COLUMNS = [
  { key: 'what_it_is',    title: 'what it is',      sub: 'each confirmed line becomes an entity in Egeria', pick: true },
  { key: 'what_it_holds', title: "what's in it",    sub: 'each becomes its own asset, related to this one' },
  { key: 'made_of',       title: "what it's made of", sub: 'components, with ports and wires derived — review stays on Architecture verdicts' },
  { key: 'relates',       title: 'how it relates',  sub: '' },
];

/* ── Page-level section nav (project owner's report after item 3 shipped:
 * "one very long page with no table of contents at the top, the sections
 * are not collapsible"). Six sections, each with a stable id the nav's
 * anchors target and each wrapped in <details>/<summary> so a viewer can
 * collapse what they are not using -- default open throughout, since the
 * reported problem was missing structure, not too much visible at once.
 * Anchor scrolling reuses classic's own convention (index.html's
 * `_curateJumpTo`/`_curateComponentAnchorId` and the diagram/dialog jumps
 * at index.html:4266/:4874): `scrollIntoView({ behavior: 'smooth',
 * block: 'center' })`. This is a page-level table of contents, a narrower
 * and separate thing from classic's component/blueprint cross-reference
 * jump -- there was no existing page-nav pattern to port, so this is new. */
/** Sections start COLLAPSED (owner, 2026-10-08) and a person's choice is remembered per viewer, like the
 *  Understanding sections (`re.understanding.collapsed.<id>`): '0' = opened by them, '1' = closed. Storage
 *  that is missing or throws just means the in-memory copy for this page load. */
const sectionKey = (id) => `re.curate.collapsed.${id}`;
function sectionOpen(id) {
  try {
    const v = globalThis.localStorage?.getItem(sectionKey(id));
    if (v === '0') return true;
    if (v === '1') return false;
  } catch { /* fall through to the in-memory copy */ }
  return !!(state.curate && state.curate.openSections && state.curate.openSections[id]);
}
function rememberSection(id, open) {
  state.curate = state.curate || {};
  (state.curate.openSections = state.curate.openSections || {})[id] = open;
  try { globalThis.localStorage?.setItem(sectionKey(id), open ? '0' : '1'); } catch { /* not remembered */ }
}

/** Which reads each section owns. Nothing is read until its section is open (owner, 2026-10-08): the page
 *  opens on six headings and a jump line, not on a minute of reads. "What it's made of" shows the blueprint
 *  SELECTOR as well as the tree, so it and "blueprints" share one blueprints read. */
const LAZY_SECTIONS = {
  'curate-sec-made-of': ['tree', 'blueprints'],
  'curate-sec-blueprints': ['blueprints'],
  'curate-sec-relates': ['deps'],
  'curate-sec-writes': ['depth'],
};

/** Open `id`, load what it owns, and bring its heading to the top under the jump line. The scroll is
 *  anchored at the section's START (a section can be thousands of px tall, so centring lands mid-list), with
 *  a scroll-margin on the section for the sticky jump line. When the section's late load lands, scroll once
 *  more, unless the person has scrolled in the meantime. A click before the plan is drawn is queued. */
export function jumpToCurateSection(id) {
  state.curate = state.curate || {};
  // With the leading tabs on, a section lives on one tab (TAB_OF_SECTION): switch to it first (in place: the plan
  // is not re-read), then open, load and scroll.
  const tab = tabOfSection(id);
  if (tab && stageTabsOn('curate', state.resourceType) && state.subTab !== tab) {
    openCurate({ tab, section: id });
    return true;
  }
  const el = document.getElementById(id);
  if (!el) { state.curate.pendingJump = id; return false; }
  if (el.tagName === 'DETAILS') { el.open = true; rememberSection(el.id, true); }
  const loading = state.curate.loadSection ? state.curate.loadSection(id) : null;
  el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  endJump();
  const j = state.curate.jump = { id, moved: false, stop: null };
  const win = globalThis.window;
  const moved = () => { j.moved = true; };
  const kinds = ['wheel', 'touchmove', 'keydown', 'mousedown', 'pointerdown'];   // a scrollbar drag or a middle-click autoscroll is the person scrolling
  if (win) kinds.forEach((k) => win.addEventListener(k, moved));
  j.stop = () => { if (win) kinds.forEach((k) => win.removeEventListener(k, moved)); };
  if (loading) {
    loading.then(() => {
      if (state.curate.jump !== j) return;
      if (!j.moved) document.getElementById(id)?.scrollIntoView({ behavior: 'auto', block: 'start' });
      endJump();
    });
  } else {
    setTimeout(() => { if (state.curate.jump === j) endJump(); }, 1500);
  }
  return true;
}
function endJump() {
  const j = state.curate && state.curate.jump;
  if (j && j.stop) j.stop();
  if (state.curate) state.curate.jump = null;
}

function bindCurateSectionNav(host) {
  host.querySelectorAll('details[id^="curate-sec-"]').forEach((d) => d.addEventListener('toggle', () => {
    rememberSection(d.id, d.open);
    if (d.open && state.curate && state.curate.loadSection) state.curate.loadSection(d.id);
  }));
}

/** Wraps a section's already-built inner HTML in the shared collapsible
 *  shell -- <summary> is the section's existing heading text, `id` is what
 *  the nav's anchors target, default COLLAPSED (see sectionOpen). */
function curateSectionHtml(id, title, extraHeader, inner) {
  return `<details id="${id}" ${sectionOpen(id) ? 'open' : ''} class="mt-s4" style="scroll-margin-top:4rem">
    <summary class="mb-s1 flex cursor-pointer items-baseline gap-s2 border-b border-rule pb-[3px]">
      <span class="font-heading text-name font-normal text-ink">${esc(title)}</span>
      ${extraHeader || ''}
    </summary>
    ${inner}
  </details>`;
}

/** A pane's read failed. A dead session is not this pane's failure: the page banner carries the one sign-in
 *  prompt, so the pane keeps only a short word. */
function paneError(lead, err, retryKey = '') {
  if (err && err.loginRequired) return '<span data-curate-signin-needed class="text-ink-muted">sign-in needed</span>';
  const retry = retryKey
    ? ` <button type="button" data-curate-retry="${esc(retryKey)}" class="cursor-pointer bg-transparent p-0 text-accent-ink underline">retry</button>` : '';
  return `${esc(lead)}${esc(err && err.message)}${retry}`;
}

/** A section whose read failed (or had nowhere to draw) is NOT loaded: reopening it, or its retry control,
 *  reads again. Only for the render this repository's loaded-record belongs to. */
function markUnloaded(slug, key) {
  const L = state.curate && state.curate.loaded;
  if (L && L.slug === slug) L[key] = false;
}

/** Is this commit record the CURRENT state of the pane? Only while it is running or queued, or when it
 *  was pressed in this session. Anything older is history: a failure from 17 days ago is not how the
 *  repository stands now. */
export function isCurrentCommit(rec) {
  if (!rec) return false;
  if (rec.state === 'running' || rec.state === 'queued') return true;
  return !!(state.curate && state.curate.pressedId && state.curate.pressedId === rec.id);
}

/** The previous commit, labelled and collapsed under the table: "last commit · 17d ago · failed at <step>". */
export function commitHistoryHtml(rec, recordHtml) {
  const failed = (rec.steps || []).find((st) => st.state === 'failed');
  const word = failed ? `failed at ${failed.name}` : (rec.state || 'recorded');
  return `<details data-commit-history class="mt-s2">
    <summary class="cursor-pointer text-provenance text-ink-muted">last commit · <span class="tnum">${esc(ago(rec.requested_at))}</span> · ${esc(word)}</summary>
    ${recordHtml}
  </details>`;
}

const SW_PREFIX = /^Software Capability\s*[·?]?\s*/;
/** "what it is" rows. The Software Capability rows come from two sources (the deployment evidence and the
 *  person's Enrichment note) and each used to read as its own "Software Capability · …" line; they sit under
 *  ONE heading, each item saying where it came from. Every row keeps its own pick (the kind is the identity). */
export function whatItIsRowsHtml(rows, picks) {
  const isSw = (r) => String(r.kind).startsWith('SoftwareCapability');
  const sw = rows.filter(isSw);
  const out = [];
  let placed = false;
  for (const r of rows) {
    if (sw.length < 2 || !isSw(r)) { out.push(curateRowHtml(r, picks.has(r.kind), true)); continue; }
    if (!placed) {
      placed = true;
      out.push(`<div data-sw-capability class="border-b border-rule">
        <div data-sw-capability-heading class="pt-s2 text-answer text-ink">Software Capability</div>
        ${sw.map((x) => {
          const from = x.source === 'enrichment' ? 'your note' : 'deployment evidence';
          const rest = String(x.label).replace(SW_PREFIX, '').trim();
          return `<div data-sw-capability-item class="pl-s3"><span class="text-provenance text-ink-muted">${from} ·</span>${
            curateRowHtml({ ...x, label: rest || 'a capability candidate' }, picks.has(x.kind), true)}</div>`;
        }).join('')}
      </div>`);
    }
  }
  return out.join('');
}

/** A state cue: the glyph from the one glyph table plus a short word, the full sentence on
 *  hover. The tone class is a literal in each branch (no class interpolation). */
export function stateCue(stateKey, word, title = '') {
  const e = stateEntry(stateKey);
  const open = e.tone === 'text-state-ok' ? '<span class="text-state-ok"'
    : e.tone === 'text-state-warn' ? '<span class="text-state-warn"' : '<span class="text-ink-muted"';
  return `${open} data-cue="${esc(stateKey)}" title="${esc(title || e.word)}"><span class="font-glyph" aria-hidden="true">${e.glyph}</span> ${esc(word)}</span>`;
}

/** What accepting did to the element's zones, from the promotion's PROOF ROW (the server read the
 *  element's zones before and after; this file never builds the sentence from the click). A short
 *  word with a cue: a promotion that failed or was refused is a warning cue, never a check. Empty
 *  when no promotion has been recorded for the row. */
export function promotionHtml(p) {
  if (!p || !p.words) return '';
  // "left as is" is neither a success nor an error: the zones were not RE's to change. The short word
  // shows; the sentence is on hover. (run_queue counts it as done, so it must not read as an error.)
  if (p.status === 'left_as_is') {
    return `<span data-promotion="left_as_is">· ${stateCue('unrun', 'left as is', p.words)}</span>`;
  }
  const ok = p.status === 'promoted' || p.status === 'already_promoted' || p.status === 'already_unzoned';
  return `<span data-promotion="${esc(p.status || '')}">· ${stateCue(ok ? 'measured' : (p.status === 'skipped' ? 'unrun' : 'error'), p.words)}</span>`;
}

/** What the mark at the left of a plan row means. It is NOT "accepted" or "published": the
 *  plan is a local read of the survey (curate_plan.py), so `candidate` only says the survey found
 *  something here that Publish would create. A word in a bordered chip, not a check, so it cannot
 *  be read as a done-state; the sentence is one hover away. */
export function rowFoundChip(r) {
  const found = !!r.candidate;
  const word = found ? 'found' : (r.count === 0 ? 'none found' : 'info only');
  const title = found
    ? 'The survey found this. Pressing Publish would create it in Egeria; nothing here has been accepted or published yet.'
    : (r.count === 0 ? 'The survey looked and found none of this.' : 'Shown for information; Publish creates nothing from this line.');
  return `<span data-row-found="${found ? 'found' : 'none'}" title="${esc(title)}"
    class="shrink-0 whitespace-nowrap rounded-sm border border-rule-strong px-2 text-provenance ${found ? 'text-ink' : 'text-ink-muted'}">${word}</span>`;
}

/** The mark beside a row's source analysis. A check here means ONLY that the analysis ran and measured
 *  this (the survey step), never that anything was accepted or published, so the word says "surveyed". */
export function sourceCue(r) {
  const key = r.state;
  const e = stateEntry(key);
  if (key === 'measured') {
    // Neutral on purpose: the green check is kept for "in Egeria". A dot in muted ink says only that
    // the survey step ran.
    const title = `The ${r.source} survey step ran and measured this. That is all the mark means: nothing here is accepted or published yet.`;
    return `<span class="text-ink-muted" data-cue="measured" title="${esc(title)}"><span class="font-glyph" aria-hidden="true">●</span> surveyed</span>`;
  }
  return stateCue(key, e.word, `State of the ${r.source} survey step: ${e.word}.`);
}

function curateRowHtml(r, selected, pick) {
  const mark = pick && r.candidate
    ? `<input type="checkbox" data-curate-pick="${esc(r.kind)}" ${selected ? 'checked' : ''}
         class="mt-[3px] shrink-0 cursor-pointer">`
    : rowFoundChip(r);
  const members = r.members?.analysis_id
    ? ` · <button type="button" data-curate-members="${esc(r.members.analysis_id)}" data-metric="${esc(r.members.metric || '')}"
          class="cursor-pointer bg-transparent p-0 text-accent-ink underline">${r.count != null ? `review ${tnum(String(r.count))}` : 'members'} ›</button>`
    : '';
  return `<div class="flex items-start gap-s2 border-b border-rule py-s2">
    ${mark}
    <div class="min-w-0 flex-1">
      <div class="text-answer text-ink">${tnum(esc(r.label))}</div>
      <div class="text-provenance text-ink-muted">
        ${sourceCue(r)} <span class="font-mono">${esc(r.source)}</span>
        ${r.evidence ? ` · ${esc(r.evidence)}` : ''}${members}</div>
    </div>
  </div>`;
}

/** The candidate folders and files "what's in it" lists (brief 2a). Nothing is ticked: each row has the two-part
 *  selector under "Publish to Egeria?", a worthy row reads "proposed" until a person includes it, and what a
 *  press sends is the selection RECORD (`scope.view`), never this list. The filter and the "show the rest"
 *  box only change which rows are shown; "include all visible" / "clear all visible" act on those. */
export function curateSubsHtml(scope, ui) {
  if (!scope || !scope.view) {
    return `<div data-curate-subs-list class="mt-s2 text-caveat text-state-warn">The selection could not be read. Reload to try again.</div>`;
  }
  const all = scope.view.rows.filter((r) => r.candidate || r.role === 'container' || r.choice);
  if (!all.length) return '';
  const shown = visibleRows(scope.view, ui);
  return `<div class="mt-s2" data-curate-subs-list>
    <div class="flex flex-wrap items-baseline gap-s3 text-provenance">
      <input type="text" placeholder="Filter by path…" value="${esc(ui.text)}" data-scope-filter
        class="border border-rule-strong bg-transparent px-[6px] py-[1px] text-caveat text-ink">
      <label class="flex cursor-pointer items-baseline gap-[4px] text-ink-muted"><input type="checkbox" data-scope-show-all ${ui.showAll ? 'checked' : ''}> show the rest (not worthy)</label>
      <span class="text-ink-muted" data-curate-subs-count><span class="tnum">${shown.length}</span> shown</span>
    </div>
    <div data-scope-slot>${scopeListHtml(scope, shown)}</div>
  </div>`;
}

/** The standing count of what is chosen for publishing, in the section header, with its cue: a choice is saved
 *  as it is made, so this stays on screen (the per-row "saved" note is only transient). */
export function savedCountHtml(view) {
  const c = contentsChosen('repo', view);   // the same function the Contents tab's label uses
  if (c.state !== 'count') return '';
  const n = c.n;
  return ` <span data-scope-saved-count class="shrink-0 whitespace-nowrap rounded-sm border border-rule-strong px-2 text-provenance text-ink" title="Each choice is saved in Resource Explorer the moment you press Include or Leave out. Nothing reaches Egeria until you press Publish.">${n} included · saved</span>`;
}

const knowDay = (iso) => String(iso || '').slice(5, 10);   // "2026-10-02T..." -> "10-02"

/** Tab 1's read-only "What we know": the confirmed Context facts (owner, sensitivity, criticality, retention,
 *  license, documentation sources), each with who declared it and when. Read from plan.know, the SAME facts
 *  "what gets written" is built from (curate_plan.context_facts). A fact that is not set is a cue plus
 *  "not declared", never blank; an interim or unsigned one shows its state. Editing stays on Context. */
export function knowBlockHtml(know) {
  const link = '<button type="button" data-goto-context class="cursor-pointer bg-transparent p-0 text-provenance text-accent-ink underline">edit on Context ›</button>';
  const who = (f) => (f.author
    ? `· declared by ${esc(f.author)}${f.set_at ? ` <span class="tnum">${esc(knowDay(f.set_at))}</span>` : ''}`
    : '');
  const rows = know.map((f) => {
    if (!f.declared) {
      return `<div class="flex flex-wrap items-baseline gap-x-s2 text-caveat" data-know-fact="${esc(f.key)}" data-declared="no">
        <span class="w-[9rem] shrink-0 text-ink-muted">${esc(f.label)}</span>${stateCue('unrun', 'not declared', `${f.label} has not been declared on Context.`)} ${link}</div>`;
    }
    const marks = `${f.interim ? ` · ${stateCue('proposal', 'interim', 'An interim value: the person who set it stood in until the owner is named.')}` : ''}${
      f.unsigned ? ` · ${stateCue('unknown', 'unsigned', UNSIGNED_LABEL)}` : ''}${
      f.review ? ` · ${stateCue('partial', 'flagged for review')}` : ''}${
      f.skipped ? ` · ${stateCue('unrun', 'will be skipped')}` : ''}`;
    if (f.key === 'documentation') {
      return `<div class="text-caveat" data-know-fact="documentation" data-declared="yes">
        <div class="flex flex-wrap items-baseline gap-x-s2"><span class="w-[9rem] shrink-0 text-ink-muted">${esc(f.label)}</span>
          <span class="tnum text-ink">${(f.sources || []).length}</span>${marks} ${link}</div>
        ${(f.sources || []).map((d) => `<div class="ml-[9rem] text-ink" data-know-source>${esc(d.label)}${d.author ? ` · declared by ${esc(d.author)}${d.set_at ? ` <span class="tnum">${esc(knowDay(d.set_at))}</span>` : ''}` : ` · ${stateCue('unknown', 'unsigned', UNSIGNED_LABEL)}`}${
          // Brief F §10: the page's Publish sends each source as an ExternalReference; the cue is the row's own reference.
          d.published === true ? ` · <span data-know-source-state="published">${stateCue('catalogued', 'published')}</span>`
          : d.published === false ? ` · <span data-know-source-state="saved">${stateCue('stored', 'saved · will publish')}</span>` : ''}</div>`).join('')}</div>`;
    }
    return `<div class="flex flex-wrap items-baseline gap-x-s2 text-caveat" data-know-fact="${esc(f.key)}" data-declared="yes">
      <span class="w-[9rem] shrink-0 text-ink-muted">${esc(f.label)}</span><span class="text-ink">${esc(f.shown)}</span>
      <span class="text-ink-muted">${who(f)}</span>${marks} ${link}</div>`;
  }).join('');
  return `<div class="mb-s1 flex flex-wrap items-baseline gap-x-s2 text-caps uppercase tracking-caps text-ink-muted"><span class="text-ink">What we know</span>
    <span class="text-provenance normal-case tracking-normal">from Context · read-only</span></div>${rows}`;
}

export function curateWritesHtml(plan, picks, subCount, containers = 0) {
  const w = plan.writes || {};
  const cls = w.classifications || [];
  const written = cls.filter((c) => !c.skipped).length;
  const lines = [];
  lines.push(`<span class="tnum">${picks.length}</span> entit${picks.length === 1 ? 'y' : 'ies'}${picks.length ? ` · ${picks.map(esc).join(', ')}` : ''}`);
  lines.push(`<span class="tnum">${subCount}</span> contained asset${subCount === 1 ? '' : 's'} you chose (files and folders)${
    containers ? ` · <span class="tnum">${containers}</span> container folder${containers === 1 ? '' : 's'} to hold them` : ''}`);
  lines.push(cls.length
    ? `<span class="tnum">${written}</span> authored classification${written === 1 ? '' : 's'} · ${cls.map((c) =>
        `${esc(c.classification)} · ${esc(c.value)} · ${esc(c.author)}${c.interim ? ' · interim' : ''}${c.skipped ? ' · <span data-plan-skipped class="text-ink-muted">will be skipped</span>' : ''}${c.review ? ' · <span class="text-state-warn">flagged for review</span>' : ''}`).join(' · ')}`
    : `no authored classifications — nothing set on the Enrichment pane yet`);
  lines.push(w.owner?.value
    ? `Owner · ${esc(w.owner.value)}${w.owner.interim ? ' · interim' : ''}`
    : `Owner · the person who publishes, as interim`);
  lines.push(w.licence ? `License · ${esc(w.licence)}` : `License · not confirmed on the Enrichment pane`);
  lines.push(`<span class="tnum">${w.survey_reports_linked || 0}</span> survey report${w.survey_reports_linked === 1 ? '' : 's'} already linked, not copied${
    w.last_published_at ? ` · last <span class="tnum">${esc(ago(w.last_published_at))}</span>${
      w.published_state === 'published_earlier' ? ` · <span title="${esc(publishedEarlierSentence(w))}">${esc(PUBLISHED_EARLIER_WORD)}</span>` : ''}` : ''}${
    w.catalogued ? ` · <span class="font-mono">${esc(String(w.asset_guid).slice(0, 8))}…</span> is the asset` : ' · no asset yet'}`);
  return lines.map((l) => `<div class="text-caveat text-ink">${l}</div>`).join('');
}

// Curate's own task/step states (done/failed/running/skipped/pending) are
// the same underlying vocabulary `factGlyph` already reads
// (measured/error/running/unrun) -- this used to declare a SECOND,
// independent glyph map, and its own '◐' for "running" disagreed with the
// canonical table's '◔' (REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §1: no
// glyph may carry two meanings). Routed through `factGlyph` (app.js) rather
// than importing glyphs.js directly, so there is exactly one place curate.js
// reaches for a state glyph.
const CURATE_TASK_TO_FACT_STATE = {
  done: 'measured', failed: 'error', running: 'running', skipped: 'unrun', pending: 'unrun',
};

function curateRecordHtml(rec, open = false) {
  if (!rec) return '';
  const g = (taskState) => factGlyph(CURATE_TASK_TO_FACT_STATE[taskState] || 'unclassified');
  // One result line (acknowledge, then validate: from the record's steps and proof rows); the steps the
  // commit took are the same data, behind "details ▸", collapsed.
  return `<div class="mt-s2 border-t border-rule pt-s2" data-curate-record="${esc(rec.id)}">
    ${commitResultHtml(rec, rec.proof_summary || null)}
    <details class="mt-s1" data-commit-details ${open ? 'open' : ''}><summary class="cursor-pointer text-provenance text-ink-muted">details ▸</summary>
    ${commitHeaderHtml(rec)}
    <div class="text-provenance text-ink-muted">published by ${esc(rec.author)} · <span class="tnum">${esc(ago(rec.requested_at))}</span>
      · ${esc(rec.state)}${rec.state === 'running' || rec.state === 'queued' ? ' · runs in the worker, not here' : ''}</div>
    <ol class="list-none pl-0">${(rec.steps || []).map((st, i) => `<li class="flex items-baseline gap-s2 text-caveat" data-commit-step="${esc(st.name)}" data-state="${esc(st.state)}">
      <span class="tnum text-ink-muted">${i + 1}.</span>
      <span class="${g(st.state).tone} font-glyph">${g(st.state).glyph}</span>
      <span class="font-mono text-ink">${esc(st.name)}</span>
      <span class="text-ink-muted">${esc(st.state)}${st.detail ? ` · ${esc(st.detail)}` : ''}</span></li>`).join('')}</ol>
    </details>
  </div>`;
}

/** Redraw the record in place (a poll tick), keeping "details ▸" open if the person opened it. */
function swapCurateRecord(host, rec) {
  const slot = host.querySelector('[data-curate-record]');
  if (!slot) return;
  const open = !!slot.querySelector('[data-commit-details]')?.open;
  slot.outerHTML = curateRecordHtml(rec, open);
}

/** Curate is a generic `/next` nav item (reachable for any resource type via
 *  `#intent-nav`), but everything it does — the plan, the commit, the
 *  component tree, the depth offers — is built entirely against
 *  `/api/projects/{slug}/...` (curate_plan.py, `registry.get(slug)` — the
 *  repo-only `projects` table). Clicking Curate for a database/filesystem
 *  today 404s with no explanation, indistinguishable from a real failure.
 *
 *  Component-tree/branch curation is genuinely repo-shaped by design (git
 *  branches, architecture-recovery components) — building a database/
 *  filesystem equivalent is a real, unscoped design question (see the PR
 *  description), not something to build speculatively here. This is the
 *  conservative fix: detect the resource type before making any repo-only
 *  call, and say so honestly — same pattern as Understanding's
 *  `nonRepoChartIndexHtml`/`loadChartsPane` gate (understanding.js) and
 *  Scouting's `renderDepthOffer` (app.js, gated on `isRepo`) for other
 *  panes that are deliberately not generalized yet. */
function nonRepoCurateHtml(entityType) {
  // Band 2 for a database or a file system (REPLY-DESIGNER-CURATE-AND-
  // UNDERSTANDING-ALL-KINDS.md §1). The component-tree and branch work is
  // repository-shaped and stays repo-only; the Findable and What-people-say
  // bands around this are the same on every kind. This replaces the old
  // "Curate isn't available for ..." body and its false claim about the
  // resource header.
  return entityType === 'filesystem' ? filesystemWorkHtml() : databaseWorkHtml();
}

/** Curate owns its host. It used to borrow `#enrichment-form`, which E1
 *  deleted from the Questions pane, and the `if (!host) return` below then
 *  drew nothing for every resource kind. The host is a SIBLING of
 *  `#question-rows`, not a child, so loadPane()'s `rows.innerHTML = ...`
 *  (which always runs for Curate) cannot wipe it. A missing pane frame is a
 *  bug and says so: it throws rather than drawing nothing. */
export function mountCurateHost() {
  // The tabbed pane (a repository with the leading tabs on) owns #curate-host itself; the Questions frame
  // (a database or a file system) mounts it as a sibling of #question-rows.
  const existing = $('curate-host');
  if (existing) return existing;
  const rows = $('question-rows');
  if (!rows) throw new Error('Curate pane host missing: #question-rows is not in the document');
  let host = $('curate-host');
  if (!host) {
    host = document.createElement('div');
    host.id = 'curate-host';
    rows.insertAdjacentElement('afterend', host);
  }
  return host;
}

/** The seconds on the plan's loading line come from this clock (a test swaps it for a fake). */
const curateClock = {
  now: () => Date.now(),
  // unref'd where the timer has it (Node under the test harness): a poll must never keep a process alive.
  every: (fn, ms) => { const id = setInterval(fn, ms); if (id && id.unref) id.unref(); return () => clearInterval(id); },
};
export function setCurateClock(c) { Object.assign(curateClock, c); }

/** The tab the Curate pane is showing. Anything that is not one of Curate's own tabs (a direct call, a stale id)
 *  reads as the first. */
export function activeCurateTab() {
  return STAGE_TAB_IDS.curate.includes(state.subTab) ? state.subTab : STAGE_TAB_IDS.curate[0];
}
/** Does this section's tab show right now? A section under a hidden tab reads nothing until its tab opens. */
function sectionTabShows(id) {
  const t = tabOfSection(id);
  return !t || t === activeCurateTab();
}

/** A repository's Curate panes: one panel per leading tab, ALL in the document, the inactive ones hidden, so a
 *  tab switch swaps `hidden` and never re-reads the plan. The Describe and Contents halves of the Publish band,
 *  and Architecture Publish, each have their own host; `draw()` fills the section hosts. */
function tabbedFrameHtml(active) {
  const hid = (tab) => (tab === active ? '' : ' hidden');
  return `<div data-curate-tab-panel="describe"${hid('curate_describe')}>
      <div data-curate-describe-head></div>
      <section data-curate-band="know" class="mb-s4 border-b border-rule pb-s2"></section>
      <section data-curate-band="findable" class="mb-s4 border-b border-rule pb-s2"></section>
      <section data-curate-band="people" class="mb-s4 border-b border-rule pb-s2"></section>
      <section data-curate-band="publish" class="mb-s4"></section>
    </div>
    <div data-curate-tab-panel="contents"${hid('curate_contents')}>
      <section data-curate-band="publish-items" class="mb-s4 border-b border-rule pb-s2"></section>
      <div data-curate-sections="contents"></div>
    </div>
    <div data-curate-tab-panel="structure"${hid('curate_structure')}>
      <section data-curate-band="architecture" class="mb-s4 border-b border-rule pb-s2"></section>
      <div data-curate-sections="structure"></div>
    </div>`;
}

/** A database's Curate panes (Brief S2): the same three panels, all in the document, the inactive ones hidden.
 *  Describe: Findable, What people say, the declared measures' publish table (declared extensibility slice 1: a
 *  Publish | Keep in RE choice per measure, carried by the commit), and the Publish to Egeria band LAST (its state
 *  line; no press). Contents:
 *  the asset row, then the scope tree and the commit panel. Structure: the proposals, the rules, the dependencies. */
function tabbedDbFrameHtml(active) {
  const hid = (tab) => (tab === active ? '' : ' hidden');
  return `<div data-curate-tab-panel="describe"${hid('curate_describe')}>
      <section data-curate-band="findable" class="mb-s4 border-b border-rule pb-s2"></section>
      <section data-curate-band="people" class="mb-s4 border-b border-rule pb-s2"></section>
      <section data-curate-band="measures" class="mb-s4 border-b border-rule pb-s2"></section>
      <section data-curate-band="publish" class="mb-s4"></section>
    </div>
    <div data-curate-tab-panel="contents"${hid('curate_contents')}>
      <section data-curate-band="publish-items" class="mb-s4"></section>
      <div data-curate-sections="contents"></div>
    </div>
    <div data-curate-tab-panel="structure"${hid('curate_structure')}>
      <div data-curate-sections="structure"></div>
    </div>`;
}

export async function renderCurate(slug) {
  finderRows = null;       // a fresh page reads the components again (a survey may have changed them)
  const frame = mountCurateHost();
  // A render token, taken before any band is set up: two renders for the SAME slug can interleave (slug equality
  // cannot tell them apart), and a non-repository render must also retire a repository render still waiting
  // for its plan. Only the newest may write shared state.
  state.curate = state.curate || {};
  const myRender = state.curate.renderToken = (state.curate.renderToken || 0) + 1;
  const superseded = () => state.curate.renderToken !== myRender;
  const entityType = apiEntityType(state.resourceType);
  // Three bands on every kind (REPLY-DESIGNER-CURATE-AND-UNDERSTANDING-ALL-
  // KINDS.md §1-2): Findable on top, the kind's own work in the middle,
  // What people say below. `host` is the middle band: everything below that
  // used to write into the whole pane now writes into it, so the repo's plan
  // view is untouched apart from its container.
  // A repository draws as three tabs (a panel each); a database or a file system as the single pane it has always been.
  const tabbed = entityType === 'repo';
  const tabbedDb = entityType === 'database' && stageTabsOn('curate', state.resourceType);
  setCommitPanelGate(null);                            // the default (always); a database's tabs set their own below
  frame.innerHTML = tabbed ? tabbedFrameHtml(activeCurateTab()) : tabbedDb ? tabbedDbFrameHtml(activeCurateTab()) : bandFrameHtml(entityType === 'filesystem');
  const host = tabbed || tabbedDb ? frame : frame.querySelector('[data-curate-band="kind"]');
  const band = (name) => frame.querySelector(`[data-curate-band="${name}"]`);
  let publishRepo = null;
  if (tabbed) publishRepo = renderPublishRepo({ describe: band('publish'), contents: band('publish-items'), structure: band('architecture') }, slug);
  const bands = [
    renderFindableBand(band('findable'), slug, entityType),
    renderPeopleBand(band('people'), slug, entityType),
    // G1: what Egeria holds for this resource, the same band on every kind. It reads RE's own records
    // first (no Egeria contact) and is independent of the repository plan below, which can take long.
    tabbed ? publishRepo.ready : tabbedDb ? renderPublishDb({ describe: band('publish'), contents: band('publish-items') }, slug) : renderPublishBand(band('publish'), slug, entityType),
    // The declared measures' publish table: RE's own rows only, never Egeria.
    tabbedDb || entityType === 'filesystem' ? renderPublishTable(band('measures'), slug, entityType) : Promise.resolve(),
  ].map((p) => p.catch((err) => {
    // A band that fails says so in its own slot; it never takes the pane down.
    const slot = band('people');
    // A dead session is said ONCE, by the page banner (session-banner.js), not by every band.
    if (slot && slot.isConnected && !err.loginRequired) slot.insertAdjacentHTML('beforeend', `<div class="text-caveat text-state-warn">A Curate band could not be drawn: ${esc(err.message)}</div>`);
  }));
  // Brief F (addendum B): after the page's ONE Publish, the sections it sent re-read their state from the rows
  // (a tag that was "saved · will publish" now reads "published 10-11"); nothing assumes it from the press.
  if (state.curateOnPublished) document.removeEventListener('re:published', state.curateOnPublished);
  state.curateOnPublished = (ev) => {
    const d = ev.detail || {};
    if (d.slug !== slug || slug !== state.selectedSlug) return;
    const f = band('findable');
    const p = band('people');
    if (f && f.isConnected) renderFindableBand(f, slug, entityType).catch(() => {});
    if (p && p.isConnected) renderPeopleBand(p, slug, entityType).catch(() => {});
    const m = band('measures');
    if (m && m.isConnected) renderPublishTable(m, slug, entityType).catch(() => {});
  };
  document.addEventListener('re:published', state.curateOnPublished);
  /** A database's tabs: the scope tree and commit panel on Contents, the placeholders and rules on Structure. The
   *  scope view is read ONCE (RE's own rows); every view it takes is announced ('re:scope-view') and the Describe
   *  tab's state line and the tab counts follow it. What READS Egeria (the commit preview, the data-class rules)
   *  waits for the tab that owns it. */
  async function renderDatabaseTabs() {
    const contentsHost = frame.querySelector('[data-curate-sections="contents"]');
    const structureHost = frame.querySelector('[data-curate-sections="structure"]');
    contentsHost.innerHTML = databaseScopeHtml();
    structureHost.innerHTML = databaseStructureHtml();
    const scopeEl = contentsHost.querySelector('[data-curate-scope]');
    setCommitPanelGate(() => !superseded() && activeCurateTab() === 'curate_contents');
    state.curate.counts = null;                         // the tab counts read "reading" until the view arrives
    let measuresHeader = null;                          // the commit header the publish table last followed
    if (state.curateOnScopeView) document.removeEventListener('re:scope-view', state.curateOnScopeView);
    state.curateOnScopeView = (ev) => {
      const d = ev.detail || {};
      if (superseded() || d.slug !== slug || slug !== state.selectedSlug || !frame.isConnected) return;
      state.curate.counts = { view: d.view || null, plan: null, planFailed: false, viewFailed: !!d.failed };
      paintDbPublishState(frame.querySelector('[data-curate-band="publish"]'), d.view || null, !!d.failed);
      // A commit carries the chosen measures and leaves their proof rows: when the commit's header moves, the
      // publish table re-reads them (the first view is the page's own draw, already read).
      const header = d.view && d.view.commit ? JSON.stringify(d.view.commit.header || null) : null;
      if (header !== null) {
        if (measuresHeader !== null && header !== measuresHeader) {
          const m = frame.querySelector('[data-curate-band="measures"]');
          if (m && m.isConnected) renderPublishTable(m, slug, 'database').catch(() => {});
        }
        measuresHeader = header;
      }
      refreshStageRail();
    };
    document.addEventListener('re:scope-view', state.curateOnScopeView);
    let rulesStarted = false;
    const startRules = () => {
      if (rulesStarted) return;
      rulesStarted = true;
      renderRulesBlock(structureHost.querySelector('[data-curate-rules]')).catch(() => {});
      state.curate.quality = renderQualityBlock(structureHost.querySelector('[data-curate-quality]'), slug);
    };
    state.curate.onTabShown = () => {
      if (superseded() || !frame.isConnected) return;
      const t = activeCurateTab();
      if (t === 'curate_contents') startScopeCommitIfShown(scopeEl);
      if (t === 'curate_structure') startRules();
    };
    if (activeCurateTab() === 'curate_structure') startRules();
    bands.push(renderCatalogueScope(scopeEl, slug).catch((err) => {
      if (scopeEl && scopeEl.isConnected) scopeEl.innerHTML = `<div class="text-caveat text-state-warn">The scope could not be drawn: ${esc(err.message)}</div>`;
    }));
    await Promise.all(bands);
  }
  if (entityType !== 'repo') {
    // Skip every repo-only /api/projects/{slug}/... call entirely rather
    // than firing it and reporting whatever 404 comes back.
    if (tabbedDb) {
      await renderDatabaseTabs();
      return;
    }
    host.innerHTML = nonRepoCurateHtml(entityType);
    if (entityType === 'database') {
      // PI-041: the read-only rules block under the glossary section; it fails inside itself.
      bands.push(renderRulesBlock(host.querySelector('[data-curate-rules]')));
      state.curate.quality = renderQualityBlock(host.querySelector('[data-curate-quality]'), slug);
      // Band 2's first section: what gets catalogued. A failure says so in
      // its own slot (renderCatalogueScope), never takes the pane down.
      bands.push(renderCatalogueScope(host.querySelector('[data-curate-scope]'), slug)
        .catch((err) => {
          const slot = host.querySelector('[data-curate-scope]');
          if (slot && slot.isConnected) slot.innerHTML = `<div class="text-caveat text-state-warn">The scope could not be drawn: ${esc(err.message)}</div>`;
        }));
    }
    await Promise.all(bands);
    return;
  }
  // The plan request can take tens of seconds on a large repository (25 s on egeria_git). The
  // stage is already drawn and the other bands are loading on their own; this slot says so and
  // counts the seconds, and blocks nothing.
  if (state.curate.lastSlug !== slug) {                // the "show all" flags are per repository
    state.blueprintShowAll = false;
    state.componentShowAll = false;
    state.curate.lastSlug = slug;
  }
  state.curate.loaded = { slug, tree: false, blueprints: false, deps: false, depth: false, p: {} };
  state.curate.loadSection = null;
  state.curate.pendingJump = null;
  endJump();
  const started = curateClock.now();
  const secs = () => Math.max(0, Math.floor((curateClock.now() - started) / 1000));
  // Where the plan's pieces are drawn: the Describe head (header line, population cue) and the section hosts of
  // Contents and Structure. Each says "plan loading" until the plan is read; one ticker counts the seconds in all.
  const hosts = {
    describe: host.querySelector('[data-curate-describe-head]'),
    know: host.querySelector('[data-curate-band="know"]'),
    contents: host.querySelector('[data-curate-sections="contents"]'),
    structure: host.querySelector('[data-curate-sections="structure"]'),
  };
  const eachHost = (fn) => Object.values(hosts).forEach((h) => h && fn(h));
  eachHost((h) => { h.innerHTML = '<div data-curate-plan-loading role="status" class="text-caveat text-ink-muted">plan loading · 0 s</div>'; });
  const stopTicker = curateClock.every(() => {
    const lines = host.querySelectorAll('[data-curate-plan-loading]');
    if (!lines.length || !host.isConnected) { stopTicker(); return; }
    lines.forEach((line) => { line.textContent = `plan loading · ${secs()} s`; });
  }, 1000);
  state.curate = state.curate || {};
  state.curate.counts = null;                          // the tab counts read "reading" until the pieces arrive
  // Before the plan arrives, a tab opening starts the prefetch of what its open sections will need (the plan read
  // can take half a minute); once the plan is drawn this is replaced by the full hook below.
  state.curate.onTabShown = () => {
    if (superseded()) return;
    const keys = [];
    for (const [id, ks] of Object.entries(LAZY_SECTIONS)) if (sectionOpen(id) && sectionTabShows(id)) keys.push(...ks, ...(ks.includes('tree') ? ['diagram'] : []));
    if (keys.length) startPrefetch(slug, keys);
  };
  const me = (state.me && (state.me.user_id || state.me.username || state.me.egeria_user)) || '';
  // What the press publishes is the selection RECORD (brief 2a), read from the server and re-read after each
  // choice; this pane keeps no selection of its own. Only the view of it (filter, "show the rest") is local.
  const ui = state.curate.scopeUi = state.curate.scopeUi || { text: '', showAll: false };
  const scope = createScopeController({
    slug, me: () => me,
    onChange: () => { if (host.isConnected && slug === state.selectedSlug) { draw(); refreshTree(); } },
    visible: () => visibleRows(scope.view, ui),
  });
  // Everything below that needs only the slug starts NOW, beside the plan, not after it.
  let scopeFailed = false;
  const scopeLoad = scope.load().catch(() => { scope.view = null; scopeFailed = true; });
  // The Contents count reads from the selection record as soon as it arrives (the plan can take far longer).
  scopeLoad.then(() => { if (!superseded() && host.isConnected) setCounts(); });
  const setCounts = () => {
    const was = state.curate.counts || {};
    state.curate.counts = { view: scope.view || null, plan: was.plan || null, planFailed: !!was.planFailed, viewFailed: scopeFailed && !scope.view };
    refreshStageRail();
  };
  // The newest commit record is read beside the plan, so the running-commit line is right after a reload.
  getLatestCuration(slug).then((out) => {
    const rec = out && out.curation;
    if (superseded() || !rec || !(rec.state === 'running' || rec.state === 'queued')) return;
    setCommitLine(commitLineFromRecord('repo', rec));
  }).catch(() => { /* the line stays blank until a press; the plan carries the record too */ });
  // Only a section the viewer left open, on the tab that shows, is read now, beside the plan; everything else
  // waits for its tab and its opening.
  const startNow = [];
  for (const [id, keys] of Object.entries(LAZY_SECTIONS)) if (sectionOpen(id) && sectionTabShows(id)) startNow.push(...keys, ...(keys.includes('tree') ? ['diagram'] : []));
  if (startNow.length) startPrefetch(slug, startNow);
  const refreshTree = () => { if (state.curate.loaded && state.curate.loaded.tree) renderComponentTree(slug); };
  let plan;
  try {
    plan = await getCuratePlan(slug);
  } catch (err) {
    stopTicker();
    if (superseded()) return;                       // a newer render owns the pane and the prefetch
    dropPrefetch();                                 // nothing will paint it: do not leave it to be read as fresh later
    state.curate.onTabShown = null;                 // and a tab opening must not start it again
    state.curate.counts = { view: scope.view || null, plan: null, planFailed: true, viewFailed: scopeFailed && !scope.view };   // Structure says "not read", not "reading"
    refreshStageRail();
    if (host.isConnected) eachHost((h) => { h.innerHTML = `<div data-curate-plan-error class="text-answer text-accent-ink">${paneError(`The plan could not be read after ${secs()} s: `, err)}</div>`; });
    return;
  }
  stopTicker();
  if (superseded()) return;
  if (slug !== state.selectedSlug) { dropPrefetch(); state.curate.onTabShown = null; return; }
  state.curate = state.curate || {};
  state.curate.blueprintCounts = ((plan.made_of || [])[0] || {}).detail || {};
  // Nothing is ticked until the owner ticks it (2026-10-08): the press sends only what was confirmed.
  const picks = new Set(state.curate.picks || []);
  let latest = (plan.commits || [])[0];
  await scopeLoad;
  if (superseded()) return;
  if (slug !== state.selectedSlug) { dropPrefetch(); return; }

  const draw = () => {
    if (superseded() || !host.isConnected) return;
    const current = isCurrentCommit(latest) ? latest : null;
    state.curate.counts = { view: scope.view || null, plan, viewFailed: scopeFailed && !scope.view };
    // Redraws keep the tree, blueprint and depth-offer nodes (open branches, groups, scroll) instead of re-reading
    // them. Each of these lives in one panel; the map is read before any panel is rewritten.
    const keep = {};
    for (const id of ['blueprint-selector', 'component-tree', 'blueprint-list', 'catalogue-depth-offer', 'curate-dependency-host']) {
      const el = host.querySelector(`#${id}`);
      if (el) keep[id] = el;
    }
    const keepScroll = host.querySelector('[data-scope-rows]')?.scrollTop || 0;
    const populationSentence = `Only worthy things get curated. Curate's population is
        disposition <em>tracking</em> or <em>using</em>; this one is <em>${esc(plan.disposition)}</em>. Set its disposition (header, or the Disposition
        sub-tab) and this screen commits. Everything below still shows what Egeria would learn.`;
    // Describe and publish: the header line, and the population as a cue and a short word (the sentence on demand).
    hosts.describe.innerHTML = `
      <div class="mb-s2 text-caveat text-ink-muted">
        <span class="text-ink">${esc(plan.technology_type)}</span> · disposition <span class="text-ink">${esc(plan.disposition)}</span>${
          plan.last_surveyed_at ? ` · surveyed <span class="tnum">${esc(ago(plan.last_surveyed_at))}</span>` : ' · never surveyed'}
      </div>
      ${plan.in_population ? '' : `<details data-curate-population-cue class="mb-s3 text-caveat">
        <summary class="cursor-pointer">${stateCue('unrun', 'not in Curate’s population', 'Only worthy things get curated: this one’s disposition is not tracking or using.')}</summary>
        <p class="mt-s1 max-w-[70ch] text-answer text-accent-ink">${populationSentence}</p></details>`}`;
    // What we know: the confirmed Context facts, read-only, from the plan's one source (curate_plan.context_facts).
    hosts.know.innerHTML = knowBlockHtml(plan.know || []);
    hosts.know.querySelectorAll('[data-goto-context]').forEach((b) => b.addEventListener('click', () => openContext()));
    // Contents: the population sentence in full, then what's in it and what gets written.
    hosts.contents.innerHTML = `
      ${plan.in_population ? '' : `<p data-curate-population class="mb-s3 max-w-[70ch] text-answer text-accent-ink">${populationSentence}</p>`}
      ${curateSectionHtml('curate-sec-what-holds', CURATE_COLUMNS[1].title,
        `<span class="text-provenance text-ink-muted">${esc(CURATE_COLUMNS[1].sub)}</span>${savedCountHtml(scope.view)}`,
        (plan.what_it_holds || []).map((r) => curateRowHtml(r, picks.has(r.kind), false)).join('')
        + curateSubsHtml(scope, ui))}
      ${curateSectionHtml('curate-sec-writes', 'what gets written',
        `<span class="text-provenance text-ink-muted">testimony copied · measurements linked · unresolved things travel</span>`,
        `${curateWritesHtml(plan, [...picks], (scope.view?.manifest?.files || 0) + (scope.view?.manifest?.folders || 0), scope.view?.manifest?.containers || 0)}
      <div class="mt-s3 max-w-[70ch] text-caveat text-ink-muted">What keeps it current: ${esc(plan.keeps_current)}</div>
      <div class="mt-s1 max-w-[70ch] text-caveat text-ink-muted">On publishing, this repository becomes an asset the rest of Egeria can see. Reversing this needs a correction, which stays on the record.</div>
      <div class="mt-s3">${repoCommitPanelHtml({
        plan, picks, scope: scope.view, fileTypePicks: curateFileTypePicks(), me,
        resurvey: !!state.curate.resurvey, sentence: CATALOG_SENTENCE, rec: current, ps: current ? (current.proof_summary || null) : null })}</div>
      ${current ? curateRecordHtml(current) : (latest ? commitHistoryHtml(latest, curateRecordHtml(latest)) : '')}
      <div id="catalogue-depth-offer"></div>`)}`;
    // Structure: what it is, what it's made of, the blueprints, how it relates.
    hosts.structure.innerHTML = `
      ${curateSectionHtml('curate-sec-what-it-is', CURATE_COLUMNS[0].title,
        `<span class="text-provenance text-ink-muted"><span class="tnum">${picks.size}</span> of <span class="tnum">${plan.what_it_is.filter((r) => r.candidate).length}</span> confirmed</span>
         <span class="text-provenance text-ink-muted">${esc(CURATE_COLUMNS[0].sub)}</span>`,
        whatItIsRowsHtml(plan.what_it_is || [], picks))}
      ${curateSectionHtml('curate-sec-made-of', CURATE_COLUMNS[2].title,
        `<span class="text-provenance text-ink-muted">${esc(CURATE_COLUMNS[2].sub)}</span>`,
        `<div id="blueprint-selector"></div>
         <div id="component-tree" class="text-caveat text-ink-muted" style="min-height:4rem">Reading the components…</div>`)}
      ${curateSectionHtml('curate-sec-blueprints', 'blueprints', '',
        `<div id="blueprint-list" style="min-height:3rem"></div>`)}
      ${curateSectionHtml('curate-sec-relates', CURATE_COLUMNS[3].title, '',
        (plan.relates || []).map((r) => curateRowHtml(r, picks.has(r.kind), false)).join('')
        + '<div class="mt-s2" id="curate-dependency-host" data-dependency-table-host></div>')}`;
    for (const [id, old] of Object.entries(keep)) host.querySelector(`#${id}`)?.replaceWith(old);
    refreshStageRail();                 // the tab labels' counts follow the same plan and view the sections show

    bindCurateSectionNav(host);
    if (state.curate.loadSection) for (const id of Object.keys(LAZY_SECTIONS)) if (document.getElementById(id)?.open) state.curate.loadSection(id);
    const rowsBox = host.querySelector('[data-scope-rows]');
    if (rowsBox) rowsBox.scrollTop = keepScroll;
    scope.bind(host);
    const drawList = () => {
      const slot = host.querySelector('[data-scope-slot]');
      if (slot && scope.view) slot.innerHTML = scopeListHtml(scope, visibleRows(scope.view, ui));
      const n = host.querySelector('[data-curate-subs-count] .tnum');
      if (n && scope.view) n.textContent = String(visibleRows(scope.view, ui).length);
    };
    host.querySelector('[data-scope-filter]')?.addEventListener('input', (ev) => { ui.text = ev.target.value; drawList(); });
    host.querySelector('[data-scope-show-all]')?.addEventListener('change', (ev) => { ui.showAll = ev.target.checked; drawList(); });
    const counts0Label = () => publishLabel(manifestCounts({ plan, picks, scope: scope.view, fileTypePicks: curateFileTypePicks() }));
    host.querySelector('[data-commit-resurvey]')?.addEventListener('change', (ev) => { state.curate.resurvey = ev.target.checked; draw(); });
    host.querySelectorAll('[data-curate-pick]').forEach((c) => c.addEventListener('change', () => {
      if (c.checked) picks.add(c.dataset.curatePick); else picks.delete(c.dataset.curatePick);
      state.curate.picks = [...picks]; draw(); refreshTree();
    }));
    host.querySelectorAll('[data-curate-members]').forEach((b) => b.addEventListener('click', () => {
      openMembers({ slug, analysisId: b.dataset.curateMembers, metric: b.dataset.metric || '', title: b.dataset.curateMembers });
    }));
    host.querySelector('[data-curate-go]')?.addEventListener('click', async (ev) => {
      const b = ev.currentTarget; b.disabled = true;
      // What the press does (workflows/curate_commit.py, brief section 1): it publishes the survey
      // already kept. It re-surveys only if the box under the button is ticked, and then only the
      // stale steps. Say which, as a cue plus a short word.
      const resurvey = !!state.curate.resurvey;
      // The press is acknowledged at once: a cue and a short word. The steps are not narrated (owner,
      // 2026-10-11); the result line below turns "confirmed" from the record's read-back.
      b.innerHTML = stateCue('running', 'publishing…', resurvey ? 'Re-surveying the stale steps first, then publishing.' : CATALOG_SENTENCE);
      const hint = b.nextElementSibling;
      if (hint) hint.textContent = '';
      try {
        const out = await curateCommit(slug, {
          confirm: [...picks], data_files: false, resurvey_stale: resurvey,   // the files and folders come from the record
        });
        plan.commits = [out.curation, ...(plan.commits || [])];
        latest = out.curation;
        setCommitLine(commitLineFromRecord('repo', out.curation));
        state.curate.pressedId = out.curation.id;     // pressed here: this one is the current state
        draw();
        await pollActivity(out.activity_id, { onTick: async () => {
          try {
            const rec = await getCuration(slug, out.curation.id);
            plan.commits[0] = rec;
            latest = rec;
            setCommitLine(commitLineFromRecord('repo', rec));
            swapCurateRecord(host, rec);
          } catch { /* the next tick will */ }
        } });
        plan.commits[0] = await getCuration(slug, out.curation.id);
        latest = plan.commits[0];
        setCommitLine(commitLineFromRecord('repo', latest));
        // A failure is a pop-up with the reason and the failed items; the persistent cue is the result line.
        const verdict = commitVerdict(latest, latest.proof_summary || null);
        if (verdict && verdict.state === 'failed') showPublishFailure(host.ownerDocument, verdict.items, () => host.querySelector('[data-curate-go]')?.click());
        try { plan.survey = (await getCuratePlan(slug)).survey || plan.survey; } catch { /* the table keeps the survey it had */ }
        try { await scope.load(); } catch { /* the rows keep the state they had; the next read will say */ }
        draw();
        state.curate.loaded.depth = true;
        renderCatalogueDepthOffer(slug, host);
      } catch (err) {
        b.disabled = false; b.textContent = counts0Label();
        if (b.nextElementSibling) b.nextElementSibling.textContent = '';
        // A refusal (401, 403, 409) needs a change of permission or state, not a retry: it says "not permitted ·"
        // (the pop-up then offers no Retry, as the tab-1 band does). Anything else may be retried.
        const why = err.status === 401 ? 'not permitted · sign in to publish'
          : (err.status === 403 || err.status === 409) ? `not permitted · ${err.message}` : `not published: ${err.message}`;
        host.querySelector('[data-curate-go]').insertAdjacentHTML('afterend', `<span data-commit-result="failed" class="text-caveat text-ink">${stateCue('partial', `not finished · ${why}`)}</span>`);
        showPublishFailure(host.ownerDocument, [{ what: 'the commit', word: why }], () => host.querySelector('[data-curate-go]')?.click());
      }
    });
  };
  draw();
  // The file types ticked in the Publish band feed the table's "file types" row.
  if (state.curateOnPicks) document.removeEventListener('re:curate-picks', state.curateOnPicks);
  state.curateOnPicks = () => { if (host.isConnected && slug === state.selectedSlug) draw(); };
  document.addEventListener('re:curate-picks', state.curateOnPicks);
  // The one Project row (Contents) answers the project; the commit blockers follow the same answer.
  if (state.curateOnProject) document.removeEventListener('re:project-changed', state.curateOnProject);
  state.curateOnProject = (ev) => {
    const d = ev.detail || {};
    if (!host.isConnected || slug !== state.selectedSlug || d.slug !== slug || !d.project) return;
    const was = plan.project || {};
    if (was.status === d.project.status && was.word === d.project.word && was.name === d.project.name) return;
    plan = { ...plan, project: d.project };      // a copy: the plan object may be shared with whoever read it
    draw();
  };
  document.addEventListener('re:project-changed', state.curateOnProject);
  // A commit already running when the page opened (a reload) is followed to its end, so the line and the record
  // move with it rather than freezing on the step they had. Bounded: it stops after about two minutes.
  if (latest && (latest.state === 'running' || latest.state === 'queued')) {
    setCommitLine(commitLineFromRecord('repo', latest));
    let ticks = 0;
    const stopFollow = curateClock.every(async () => {
      if (!host.isConnected || superseded() || slug !== state.selectedSlug || ++ticks > 40) { stopFollow(); return; }
      try {
        const rec = await getCuration(slug, latest.id);
        plan.commits[0] = rec; latest = rec;
        setCommitLine(commitLineFromRecord('repo', rec));
        swapCurateRecord(host, rec);
        if (rec.state !== 'running' && rec.state !== 'queued') { stopFollow(); draw(); }
      } catch { /* the next tick will */ }
    }, 3000);
  }
  if (superseded()) return;
  // A tab opening starts what its open sections own (and mounts Architecture Publish on Structure).
  state.curate.onTabShown = () => {
    if (superseded() || !host.isConnected) return;
    for (const id of Object.keys(LAZY_SECTIONS)) if (document.getElementById(id)?.open) loadSectionData(id);
    if (activeCurateTab() === 'curate_structure' && publishRepo) publishRepo.mountStructure();
  };
  setupLazySections();
  if (publishRepo && activeCurateTab() === 'curate_structure') publishRepo.mountStructure();
  const pending = state.curate.pendingJump;
  if (pending) { state.curate.pendingJump = null; jumpToCurateSection(pending); }

  /** Reads one section's data the first time it is open; a cue shows in its slot at once. A read that fails
   *  is NOT remembered as loaded: reopening the section, or the retry control, reads again. A read already in
   *  flight is joined, so a jump into it still waits for it. */
  function loadSectionData(id) {
    const keys = LAZY_SECTIONS[id];
    const L = state.curate.loaded;
    if (!keys || !L || L.slug !== slug || superseded()) return null;
    // A section under a hidden tab reads nothing until its tab opens (onTabShown starts it then).
    if (!sectionTabShows(id)) return null;
    const work = [];
    const cueIn = (el, word) => { if (el) el.innerHTML = `<span class="text-caveat">${stateCue('running', word)}</span>`; };
    const track = (k, promise) => {
      L.p[k] = promise.then((ok) => { if (ok === false) L[k] = false; return ok; }, () => { L[k] = false; return false; })
        .finally(() => { if (L.p[k] === tracked) delete L.p[k]; });
      const tracked = L.p[k];
      work.push(tracked);
    };
    for (const k of keys) {
      if (L[k]) { if (L.p[k]) work.push(L.p[k]); continue; }
      L[k] = true;
      if (k === 'tree') {
        startPrefetch(slug, ['tree', 'diagram']);
        cueIn($('component-tree'), 'loading components');
        track(k, Promise.resolve(renderComponentTree(slug)));
      } else if (k === 'blueprints') {
        startPrefetch(slug, ['blueprints']);
        cueIn($('blueprint-list'), 'loading blueprints');
        track(k, Promise.resolve(renderBlueprintList(slug)));
      } else if (k === 'deps') {
        const slot = host.querySelector('[data-dependency-table-host]');
        if (!slot) { L[k] = false; continue; }                      // nowhere to draw: not loaded
        cueIn(slot, 'loading dependencies');
        // Brief section 3: the same ONE table, where a person confirms the proposed runtime rows.
        // A read that is REJECTED (not merely answered with an error) gets the error slot and a retry, like the tree.
        track(k, Promise.resolve().then(() => mountDependencyTable(slot, slug, { confirmable: true, me })).then((r) => {
          if (r === null && slot.isConnected) {
            slot.insertAdjacentHTML('beforeend', ' <button type="button" data-curate-retry="deps" class="cursor-pointer bg-transparent p-0 text-accent-ink underline">retry</button>');
            return false;
          }
          return true;
        }, (err) => {
          if (slot.isConnected) slot.innerHTML = `<span data-dependency-error class="text-caveat text-accent-ink">${paneError('The dependencies could not be read: ', err, 'deps')}</span>`;
          return false;
        }));
      } else if (k === 'depth') {
        startPrefetch(slug, ['depth']);
        track(k, Promise.resolve(renderCatalogueDepthOffer(slug, host)).then(() => true));
      }
    }
    return work.length ? Promise.all(work).then(() => undefined) : null;
  }
  const SECTION_OF = { tree: 'curate-sec-made-of', blueprints: 'curate-sec-blueprints', deps: 'curate-sec-relates' };
  host.addEventListener('click', (ev) => {
    const b = ev.target.closest && ev.target.closest('[data-curate-retry]');
    if (!b) return;
    const k = b.dataset.curateRetry;
    // A read already in flight is joined, not started a second time: only a settled section is reset.
    if (state.curate.loaded && !(state.curate.loaded.p && state.curate.loaded.p[k])) state.curate.loaded[k] = false;
    loadSectionData(SECTION_OF[k] || '');
  });
  function setupLazySections() {
    if (superseded()) return;
    state.curate.loadSection = loadSectionData;
    for (const id of Object.keys(LAZY_SECTIONS)) if (document.getElementById(id)?.open) loadSectionData(id);
  }
}

/** Reads that need only the slug, started beside the plan request and consumed once by the first paint.
 *  A prefetched promise that nobody takes (the plan failed) must not become an unhandled rejection. */
let prefetched = null;
const PREFETCH_TTL_MS = 60000;
/** True while a started prefetch has not been consumed or dropped (a test reads it). */
export function curatePrefetchPending() { return !!prefetched; }
function dropPrefetch() { prefetched = null; }
/** Start the reads a section will need, the moment it is going to open (or at page open for a section the
 *  viewer left open). `diagram` is the architecture-diagram fact, started beside the tree rather than after it. */
function startPrefetch(slug, keys) {
  const keep = (p) => { p.catch(() => {}); return p; };
  const make = {
    tree: () => getComponentTree(slug, ''),
    diagram: () => getBulkFacts([slug], ['architecture_diagram'], apiEntityType(state.resourceType)),
    blueprints: () => getComponentBlueprints(slug),
    depth: () => getCatalogueDepthOffer(slug),
  };
  if (!prefetched || prefetched.slug !== slug) prefetched = { slug, at: Date.now() };
  for (const k of keys) if (make[k] && !prefetched[k]) prefetched[k] = keep(make[k]());
}
function takePrefetched(slug, key) {
  if (!prefetched || prefetched.slug !== slug || !prefetched[key]) return null;
  if (Date.now() - prefetched.at > PREFETCH_TTL_MS) { prefetched = null; return null; }
  const p = prefetched[key];
  prefetched[key] = null;
  return p;
}

/** The file types ticked in the Publish band (a Set of labels), kept on the Curate state. */
function curateFileTypePicks() {
  state.curate = state.curate || {};
  if (!(state.curate.fileTypePicks instanceof Set)) state.curate.fileTypePicks = new Set();
  return state.curate.fileTypePicks;
}

/* ── The layer-2 catalogue-depth offer ────────────────────────────────────
 *
 * DepthOffer's three rules (FUNNEL-COST-RULINGS §3), applied to promoting
 * accepted architecture-recovery verdicts into real Egeria components
 * instead of running never-run analyses (owner's ruling, 2026-09-15, on
 * REPLY-CATALOGUE-IN-LAYERS.md §3):
 *
 *   not a nag   — offered once per catalogue record, in the pane, never a
 *                 modal (the backend refuses a second write on the same
 *                 record; already_decided is the UI's own courtesy check).
 *   not a gate  — layer 1 is already committed by the time this appears;
 *                 nothing here waits on an answer.
 *   not a scold — "N components recovered, M not catalogued" is a fact
 *                 about the record. No imperative sentence; the reader
 *                 decides whether it matters.
 *
 * Unlike DepthOffer, "accepted" here has no per-item choice to make: the
 * accept/reject decision already happens branch by branch in the component
 * tree (recordVerdicts). So the offer's one action is a link that opens the
 * tree, not a queue-in-background button — "choose which" would be asking
 * the reader to redo a decision the tree already offers properly.
 */
async function renderCatalogueDepthOffer(slug, host) {
  const slot = host.querySelector('#catalogue-depth-offer');
  if (!slot) return;
  let offer;
  try { offer = await (takePrefetched(slug, 'depth') || getCatalogueDepthOffer(slug)); } catch { slot.innerHTML = ''; return; }
  if (slug !== state.selectedSlug) return;   // a faster click, or a different resource, won
  if (!offer.layer1_done || offer.already_decided || !offer.remaining_components) { slot.innerHTML = ''; return; }

  const priceLine = () => {
    const c = offer.cost || {};
    if (c.basis !== 'measured') return `<span class="text-ink-muted">${esc(c.sentence || 'not yet measured')}</span>`;
    return `<span class="tnum">${esc(fmtSeconds(c.seconds))}</span> <span class="text-ink-muted">${esc(c.sentence.replace(/^about [^(]+/, '').trim())}</span>`;
  };
  slot.innerHTML = `
    <div data-catalogue-depth-offer class="mt-s3 border-t border-rule pt-s2">
      <div class="text-caveat text-ink"><span class="tnum">${offer.total_components}</span> component${offer.total_components === 1 ? '' : 's'} recovered ·
        <span class="tnum">${offer.remaining_components}</span> not published.</div>
      <div class="mt-s2 flex flex-wrap items-baseline gap-s3 text-caveat">
        <button data-catalogue-depth="accepted" class="cursor-pointer bg-transparent p-0 text-accent-ink underline"
          >publish the next layer · <span class="tnum">${offer.remaining_components}</span> component${offer.remaining_components === 1 ? '' : 's'} · ${priceLine()} ›</button>
        <button data-catalogue-depth="declined" class="cursor-pointer bg-transparent p-0 text-provenance text-ink-muted underline">Not now</button>
        <span data-catalogue-depth-status class="text-provenance text-ink-muted"></span>
      </div>
    </div>`;
  const box = slot.querySelector('[data-catalogue-depth-offer]');
  const status = box.querySelector('[data-catalogue-depth-status]');
  const finish = async (outcome) => {
    try {
      await postCatalogueDepthOfferOutcome(slug, offer.curation_id, outcome);
    } catch (err) {
      status.innerHTML = `<span class="text-accent-ink">${
        err.status === 401 ? 'not recorded — sign in to answer the offer' : `not recorded: ${esc(err.message)}`}</span>`;
      return;
    }
    if (outcome === 'declined') {
      box.innerHTML = `<div class="text-provenance text-ink-muted">not now · on the publish record</div>`;
    } else {
      box.remove();
      document.getElementById('component-tree')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  };
  box.querySelector('[data-catalogue-depth="declined"]').addEventListener('click', () => finish('declined'));
  box.querySelector('[data-catalogue-depth="accepted"]').addEventListener('click', () => finish('accepted'));
}



/* ── Component review at the branch ──────────────────────────────────────
 *
 * The designer's ports round (2026-09-14). Rows are branches of the path
 * the components are keyed by -- kafka's 642 become 71 -- and the decision
 * is made at the branch: a branch verdict inherits, a component's own
 * wins, and an inherited one says so (accepted · with pyegeria/) rather
 * than posing as a decision someone made about that file. Confidence
 * routes; it never hides: the ⚠ count rides on the branch. Grouping nodes
 * stay marked with the classic UI's words. Ports are two words on the row
 * where they exist, nothing where they do not, and one sentence at the
 * foot when a repository declares none, saying what it looked in. Bulk
 * accept goes through the shared preview dialog (rule 4): nothing runs
 * until confirmed. No undo, and the word is not offered -- a verdict is a
 * new row and the trail keeps both; the word is change. */
function verdictBadge(v, inEgeria = null) {
  if (!v) return `<span class="text-ink-muted">undecided</span>`;
  const word = esc(v.verdict);
  const only = v.only ? ` <span class="text-ink-muted">· this component only</span>` : '';
  return (v.inherited_from
    ? `<span class="text-ink">${word}</span> <span class="text-ink-muted">· with <span class="font-mono">${esc(v.inherited_from)}/</span></span>`
    : `<span class="text-ink">${word}</span>${only}${v.decided_by ? ` <span class="text-ink-muted">· ${esc(v.decided_by)}</span>` : ''}`)
    + egeriaWordHtml(v.verdict, inEgeria);
}

/** What a decision means for Egeria, as a cue plus a short word, the sentence on hover. `inEgeria` is the
 *  element's own cache row (true/false), or null when the row cannot say (a branch). Accepting is a decision:
 *  it reaches Egeria when Publish is pressed. Rejecting never writes: RE cannot remove an element. */
export function egeriaWordHtml(verdict, inEgeria) {
  if (inEgeria === null || inEgeria === undefined) return '';
  if (verdict === 'accepted') {
    return inEgeria
      ? ` <span data-in-egeria="yes">· ${stateCue('measured', 'in Egeria')}</span>`
      : ` <span data-in-egeria="no">· ${stateCue('unrun', 'not in Egeria yet', 'Accepting is a decision. Nothing is written to Egeria until Publish is pressed.')}</span>`;
  }
  if (verdict === 'rejected' && inEgeria) {
    return ` <span data-in-egeria="still">· ${stateCue('partial', 'still in Egeria', 'Rejecting writes nothing: Resource Explorer cannot remove an element from Egeria, so it stays there until a steward removes it.')}</span>`;
  }
  return '';
}

/** The column has two shapes (designer, round two): one or two ports are
 *  spelled out -- `8000 in, routes`; three or more become `15 ports ›`,
 *  opening the list in the rail, the way every other count in this app
 *  opens what it counted. `key` names the row so the click can find it. */
function portsWords(n, own, key) {
  if (own && own.length) {
    if (own.length <= 2) return `<span class="text-ink-muted">· ${own.map((p) => `${esc(p.name)}${p.direction ? ` ${esc(p.direction)}` : ''}`).join(', ')}</span>`;
    return `<span class="text-ink-muted">· <button data-ports-open="${esc(key)}" class="cursor-pointer bg-transparent p-0 text-accent-ink underline"><span class="tnum">${own.length}</span> ports${icon('chevron-right', { size: 12 })}</button></span>`;
  }
  return n ? `<span class="text-ink-muted">· <span class="tnum">${n}</span> port${n === 1 ? '' : 's'} declared below</span>` : '';
}

/** The rail: a component's declared ports, read from the artifacts. No
 *  verdict to give -- a port is a line in a Dockerfile. */
function openPortsInRail(slug, key, ports) {
  ensureRailShowing();
  railClaim();
  railFrame('Ports', slug, `
    <div class="mb-s1 text-caps text-chrome-muted"><span class="font-mono">${esc(key)}</span> · read from the deployment artifacts · no verdict to give</div>
    ${ports.map((p) => `<div class="flex items-baseline gap-s2 border-b border-chrome-line-soft py-[3px] text-caps">
      <span class="font-mono text-chrome-ink">${esc(p.name)}</span>
      ${p.direction ? `<span class="text-chrome-muted">${esc(p.direction)}</span>` : ''}
      ${p.protocol ? `<span class="text-chrome-muted">${esc(p.protocol)}</span>` : ''}
    </div>`).join('')}`, { sub: `${ports.length} declared` });
}

/** "this component only" or "with its N children" for a branch row (owner, 2026-10-09). The default is the
 *  component alone when the branch is itself a component, and everything under it when it is only a grouping.
 *  Kept per repository so a redraw does not lose the choice. */
export function branchScopeMode(slug, b) {
  const chosen = state.curate && state.curate.branchScope && state.curate.branchScope.slug === slug
    ? state.curate.branchScope.modes[b.path] : '';
  if (b.grouping_only || !b.children) return b.grouping_only ? 'with' : 'only';   // no choice to make
  return chosen === 'with' || chosen === 'only' ? chosen : 'only';
}
function setBranchScopeMode(slug, path, mode) {
  state.curate = state.curate || {};
  if (!state.curate.branchScope || state.curate.branchScope.slug !== slug) state.curate.branchScope = { slug, modes: {} };
  state.curate.branchScope.modes[path] = mode;
}
/** How many components a verdict on this branch row reaches, given its mode. */
export const branchReach = (b, mode) => (mode === 'only' ? 1 : (b.components || 0));

/** A leaf row read as the branch it is when components sit beneath it: the same fields a branch row carries,
 *  so the leaf's choice, count and press go through the SAME functions as the branch row's (one source). */
export const leafAsBranch = (l) => ({ path: l.path, grouping_only: false, children: l.children || 0,
  components: (l.children || 0) + 1, low_confidence: l.reach_low || 0, accepted: l.reach_accepted || 0 });

/** What a press on a row with the choice sends and counts: its mode, how many it reaches (named before the
 *  press), the low-confidence and already-accepted counts, and the scope marked "only" when it is the
 *  component alone. Used by the branch row, the leaf row and the bulk bar. */
export function scopedVerdictOptions(slug, b) {
  const mode = branchScopeMode(slug, b);
  return { mode, count: branchReach(b, mode), low: mode === 'only' ? 0 : (b.low_confidence || 0),
    exists: mode === 'only' ? 0 : (b.accepted || 0), onlyScopes: mode === 'only' ? [b.path] : [] };
}

/** "applies to this component only / with its N children" -- the toggle pair, for a branch row (`attr`
 *  data-branch-scope) or a leaf row (data-leaf-scope). Nothing when there is no choice to make. */
export function scopeChoiceHtml(b, mode, attr = 'data-branch-scope') {
  if (b.grouping_only || !(b.children > 0)) return '';
  const toggle = (m, label) => `<button type="button" ${attr}="${m}" data-path="${esc(b.path)}" aria-pressed="${mode === m ? 'true' : 'false'}"
        class="cursor-pointer bg-transparent p-0 ${mode === m ? 'text-ink' : 'text-accent-ink underline'}">${mode === m ? '● ' : ''}${label}</button>`;
  return `<span ${attr}-choice class="text-ink-muted">· applies to
        ${toggle('only', 'this component only')} /
        ${toggle('with', `with its <span class="tnum">${b.children}</span> children`)}</span>`;
}

function branchRowHtml(b, selected, mode = 'with', matches = null) {
  // The branch's own type leads; the mix beneath it is the CHILDREN's, so
  // a branch whose only typed component is itself does not say it twice.
  const mix = Object.entries(b.types || {}).map(([t, n]) => [t, t === b.type ? n - 1 : n]).filter(([, n]) => n > 0);
  const types = mix.map(([t, n]) => `${esc(t)}${n > 1 ? ` <span class="tnum">×${n}</span>` : ''}`).join(', ');
  const reach = branchReach(b, mode);
  return `<div class="${selected ? 'border-b border-l-2 border-rule border-l-accent bg-accent-tint py-[5px] pl-s1' : 'border-b border-rule py-[5px]'}" data-branch="${esc(b.path)}" data-selected="${selected ? '1' : '0'}">
    <div class="flex flex-wrap items-baseline gap-x-s2 gap-y-[2px]">
      <input type="checkbox" data-branch-select="${esc(b.path)}" ${selected ? 'checked' : ''}
        aria-label="select ${esc(b.name)}" class="shrink-0 cursor-pointer">
      ${selected ? '<span data-selected-cue class="text-provenance text-ink" title="Ticked: the selection bar acts on every ticked row.">● selected</span>' : ''}
      <button data-branch-open="${esc(b.path)}" class="cursor-pointer bg-transparent p-0 font-mono text-caveat text-ink">${esc(b.name)}/${icon('chevron-right', { size: 12 })}</button>
      <span class="text-provenance text-ink-muted">· <span class="tnum">${b.components}</span> component${b.components === 1 ? '' : 's'}</span>
      ${matches === null ? '' : `<span data-branch-matches class="text-provenance text-ink">· <span class="tnum">${matches}</span> match</span>`}
      ${b.grouping_only ? `<span class="text-provenance text-ink-muted">· grouping only — a directory that holds components, not a component itself</span>` : b.type ? `<span class="text-provenance text-ink-muted">· ${esc(b.type)}</span>` : ''}
      ${types ? `<span class="text-provenance text-ink-muted">· ${types}</span>` : ''}
      ${b.low_confidence ? `<span class="text-provenance text-state-warn">· ⚠ <span class="tnum">${b.low_confidence}</span> at or below 50%</span>` : ''}
      ${portsWords(b.ports, b.own_ports, b.path)}
    </div>
    <div class="mt-[2px] flex flex-wrap items-baseline gap-x-s3 text-provenance">
      <span>${verdictBadge(b.verdict)}</span>
      <span class="text-ink-muted"><span class="tnum">${b.accepted}</span> accepted · <span class="tnum">${b.rejected}</span> rejected · <span class="tnum">${b.undecided}</span> undecided</span>
      ${b.accepted ? `<span class="text-ink-muted" data-branch-in-egeria>· <span class="tnum">${b.in_egeria || 0}</span> of them in Egeria${
        b.rejected_in_egeria ? ` · <span class="tnum">${b.rejected_in_egeria}</span> rejected but still in Egeria` : ''}</span>` : ''}
      ${scopeChoiceHtml(b, mode)}
      <button data-branch-verdict="accepted" data-scope="${esc(b.path)}" class="cursor-pointer bg-transparent p-0 text-accent-ink underline">${mode === 'only' ? 'accept this component' : `accept all ${reach}`}</button>
      <button data-branch-verdict="rejected" data-scope="${esc(b.path)}" class="cursor-pointer bg-transparent p-0 text-ink-muted underline">${mode === 'only' ? 'reject this component' : 'reject all'}</button>
    </div>
    <div data-branch-leaves hidden class="mt-s1 pl-s3"></div>
  </div>`;
}

/** RULING-WHAT-A-VERDICT-IS-ABOUT.md §2a/§2b/§2c. When two extractors
 *  currently propose this path, both are shown -- not whichever wrote last
 *  -- with the agreement line that is the strongest signal the recovery
 *  has. `withdrawn_by` flags an accepted verdict whose extractor no longer
 *  proposes the path; it never invalidates the verdict itself. `run_label`
 *  ("detect"/"coupling") renders as "found by"; `perspective` (physical/
 *  deployment/logical/dev) renders as "reading" -- two different axes that
 *  used to share one word (§0). */
export function leafRowHtml(l, mode = 'only') {
  // A leaf that is itself a component with components beneath it carries the branch row's choice.
  const lb = leafAsBranch(l);
  const choice = lb.children > 0;
  const withKids = choice && mode === 'with';
  const multi = (l.proposals || []).length >= 2;
  const proposalLines = multi ? l.proposals.map((p) => `
    <div class="pl-s2 text-provenance text-ink-muted">found by ${esc(p.run_label)}${p.type ? ` — ${esc(p.type)}` : ''} · ${esc(p.perspective || 'physical')} reading · confidence <span class="tnum">${p.confidence ?? 0}</span>%</div>
  `).join('') : '';
  const agreementLine = l.agreement
    ? `<div class="pl-s2 text-provenance text-accent-ink">two extractors agree this is a component</div>` : '';
  const withdrawnLine = (l.withdrawn_by || []).length
    ? `<div class="pl-s2 text-provenance text-state-warn">⚠ review — no longer proposed by ${esc(l.withdrawn_by.join(', '))}</div>` : '';
  return `<div data-leaf-path="${esc(l.path)}" data-leaf-undecided="${isUndecidedLeaf(l) ? '1' : '0'}" class="flex flex-col gap-[1px] border-b border-rule py-[3px]">
    <div class="flex flex-wrap items-baseline gap-x-s2 text-provenance">
      <span class="font-mono text-ink">${esc(l.path.split('/').pop())}</span>
      ${!multi ? `<span class="text-ink-muted">· ${l.type ? esc(l.type) : 'type not assigned · boundary only'}</span>` : ''}
      ${!multi && (l.low_confidence ? `<span class="text-state-warn">· ⚠ confidence <span class="tnum">${l.confidence ?? 0}</span>%</span>` : l.confidence != null ? `<span class="text-ink-muted">· confidence <span class="tnum">${l.confidence}</span>%</span>` : '')}
      ${l.ports?.length ? portsWords(0, l.ports, l.path) : ''}
      ${admissionHtml(l)}
      <span>· ${verdictBadge(l.verdict, l.verdict ? !!l.materialized : null)}</span>
      ${promotionHtml(l.promotion)}
      ${choice ? scopeChoiceHtml(lb, mode, 'data-leaf-scope') : ''}
      <button data-leaf-verdict="accepted" data-scope="${esc(l.path)}" class="cursor-pointer bg-transparent p-0 text-accent-ink underline">${withKids ? `accept all ${branchReach(lb, mode)}` : (l.verdict || {}).verdict ? 'change' : 'accept'}</button>
      <button data-leaf-verdict="rejected" data-scope="${esc(l.path)}" class="cursor-pointer bg-transparent p-0 text-ink-muted underline">${withKids ? 'reject all' : 'reject'}</button>
      <button data-leaf-blueprints="${esc(l.path)}" title="Which candidate blueprints this component is a member of" class="cursor-pointer bg-transparent p-0 text-accent-ink underline">blueprints${icon('chevron-right', { size: 12 })}</button>
    </div>
    <div data-leaf-bp-line class="pl-s2 text-provenance"></div>
    ${proposalLines}${agreementLine}${withdrawnLine}
  </div>`;
}

/** A branch's leaves grouped by scope-hierarchy cluster (designer, 2026-09-17
 *  — see the addendum in ITEM-3-CURATE-IMPLEMENTED.md).
 *  `packages/` alone held 64 of 69 components as one flat list; the same
 *  clustering that already groups the blueprints panel's "scope-hierarchy ·
 *  collection" rows (`component_tree.group_leaves`, reading `scope_hierarchy.
 *  derive()`) turns that into ~8 groups of ~10 here too. Default OPEN when
 *  the group still has undecided work, default CLOSED once it is fully
 *  decided — the depth-1 accepted signal a reader used to get from the flat
 *  list is still here, just per-group instead of per-branch. */
/** A leaf with no verdict of its own (an inherited one is the branch's, not a decision about this leaf). */
export const isUndecidedLeaf = (l) => !l.verdict || !l.verdict.verdict || !!l.verdict.inherited_from;

function leafGroupHtml(g, openByName = null, modeOf = () => 'only') {
  const todo = g.members.filter(isUndecidedLeaf).length;
  // A group the person was already working in keeps the state it had; only a first view uses the default.
  const open = openByName && openByName.has(g.name) ? openByName.get(g.name) : g.undecided > 0;
  return `<details data-leaf-group="${esc(g.name)}" class="border-b border-rule py-[3px]" ${open ? 'open' : ''}>
    <summary class="cursor-pointer text-provenance">
      <span class="font-mono text-ink">${esc(g.name)}/</span>
      <span class="text-ink-muted">· <span class="tnum">${g.accepted}</span> accepted ·
        <span class="tnum">${g.rejected}</span> rejected · <span class="tnum">${g.undecided}</span> undecided</span>
    </summary>
    <div class="pl-s3">
      <div class="flex flex-wrap items-baseline gap-x-s3 py-[2px] text-provenance">
        <button data-group-verdict="accepted" data-group="${esc(g.name)}" ${todo ? '' : 'disabled'} title="${todo ? 'Only the undecided ones are recorded' : 'Every one already has a verdict'}" class="cursor-pointer bg-transparent p-0 text-accent-ink underline disabled:cursor-default disabled:opacity-60">accept all ${todo}</button>
        <button data-group-verdict="rejected" data-group="${esc(g.name)}" ${todo ? '' : 'disabled'} title="${todo ? 'Only the undecided ones are recorded' : 'Every one already has a verdict'}" class="cursor-pointer bg-transparent p-0 text-ink-muted underline disabled:cursor-default disabled:opacity-60">reject all ${todo}</button>
      </div>
      ${g.members.map((l) => leafRowHtml(l, modeOf(l))).join('')}</div>
  </details>`;
}

/** The tree's own checkbox selection (SPEC-CURATE-SELECTION-AND-BLUEPRINTS.md
 *  §1). No select-mode toggle -- the spec is explicit that the tree's rows
 *  are already a work queue, unlike the sidebar's navigation rows, so the
 *  checkboxes are simply present. Keyed per-slug so switching resources does
 *  not carry a stale selection into a different repository's tree. */
function curateSelectionSet(slug) {
  if (!state.curateSelection || state.curateSelection.slug !== slug) {
    state.curateSelection = { slug, paths: new Set() };
  }
  return state.curateSelection.paths;
}

function selectionBarHtml(selected, shown, total) {
  if (!total) return '';
  const selectedShown = shown.filter((b) => selected.has(b.path)).length;
  const overflow = selected.size > selectedShown ? selected.size - selectedShown : 0;
  return `<div data-selection-bar data-sticky="${selected.size ? '1' : '0'}" class="${selected.size
    ? 'sticky top-0 z-10 mb-s1 flex flex-wrap items-baseline gap-s3 border-b border-rule bg-paper py-s1 text-provenance'
    : 'mb-s1 flex flex-wrap items-baseline gap-s3 text-provenance'}">
    <label class="flex cursor-pointer items-baseline gap-[5px] text-ink-muted">
      <input type="checkbox" data-select-all-shown ${shown.length && selectedShown === shown.length ? 'checked' : ''}>
      select all shown</label>
    ${total > shown.length ? `<button data-select-all-matching class="cursor-pointer bg-transparent p-0 text-accent-ink underline"
        >select all <span class="tnum">${total}</span> branches${icon('chevron-right', { size: 12 })}</button>` : ''}
    <span class="${selected.size ? 'text-ink' : 'text-ink-muted'}" data-selected-count>${selected.size ? '● ' : ''}<span class="tnum">${selectedShown}</span> of <span class="tnum">${shown.length}</span> shown selected${
      overflow ? ` · <span class="tnum">${selected.size}</span> selected in total` : ''}</span>
    ${selected.size ? `<button data-selection-verdict="accepted" class="cursor-pointer bg-transparent p-0 text-accent-ink underline"
        >accept <span class="tnum">${selected.size}</span> selected</button>
      <button data-selection-verdict="rejected" class="cursor-pointer bg-transparent p-0 text-ink-muted underline"
        >reject <span class="tnum">${selected.size}</span></button>
      <button data-selection-clear class="cursor-pointer bg-transparent p-0 text-ink-muted underline">clear</button>` : ''}
  </div>`;
}

/* ── Finding a component, and reading it by reading (PI-072) ────────────────
 * The tree names branches; a search needs every component. `/components/leaves?branch=` with the empty branch
 * is the whole repository, through the same rows a branch shows, read once per resource and only when a
 * search or the readings are asked for. A reading is the Perspective a proposal was made in (physical,
 * deployment, logical, dev); a component with no proposal on record has no reading and says so. */
let finderRows = null;   // { slug, rows, total, truncated }
async function readFinder(slug) {
  if (finderRows && finderRows.slug === slug) return finderRows;
  const out = await getComponentFinder(slug);
  finderRows = { slug, rows: out.leaves || [], total: out.total ?? (out.leaves || []).length, truncated: !!out.truncated };
  return finderRows;
}
/** The readings a component was proposed in; '' stands for none recorded. */
export function readingsOf(row) {
  if (Array.isArray(row.readings)) return row.readings.length ? row.readings : [''];
  const ps = [...new Set((row.proposals || []).map((p) => p.perspective || 'physical'))];
  return ps.length ? ps : [''];
}
/** Does a component match the search text (name, path or type) and the reading ('' = every reading)? */
export function componentMatches(row, search, reading) {
  const q = String(search || '').trim().toLowerCase();
  if (q && ![row.name, row.path, row.type].some((v) => String(v || '').toLowerCase().includes(q))) return false;
  if (reading === '*none*') return readingsOf(row).includes('');
  if (reading && !readingsOf(row).includes(reading)) return false;
  return true;
}
const NO_READING = '*none*';

function findControlsHtml({ search, readingPick, finder, finderError, filtering, matched, shownBranches, allBranches, total, cut }) {
  const readingsOpen = !!state.componentReadingsOpen || !!readingPick;
  let readings;
  if (!readingsOpen) {
    readings = `<button type="button" data-tree-readings-open title="Group the components by the reading each was proposed in"
      class="cursor-pointer bg-transparent p-0 text-accent-ink underline">by reading${icon('chevron-right', { size: 12 })}</button>`;
  } else if (finder) {
    const counts = new Map();
    for (const r of finder) for (const rd of readingsOf(r)) counts.set(rd, (counts.get(rd) || 0) + 1);
    const chip = (value, label, n) => {
      const on = (readingPick || '') === value;
      return `<button type="button" data-tree-reading="${esc(value)}" aria-pressed="${on ? 'true' : 'false'}"
        class="cursor-pointer bg-transparent p-0 ${on ? 'text-ink' : 'text-accent-ink underline'}">${on ? '● ' : ''}${esc(label)} <span class="tnum">${n}</span></button>`;
    };
    readings = `<span data-tree-readings>reading: ${chip('', 'all', total)}${
      [...counts.keys()].filter(Boolean).sort().map((r) => ` / ${chip(r, r, counts.get(r))}`).join('')}${
      counts.get('') ? ` / ${chip(NO_READING, 'no reading recorded', counts.get(''))}` : ''}</span>`;
  } else {
    readings = `<span data-tree-readings>${stateCue('unknown', 'readings not read', finderError)}</span>`;
  }
  let status = '';
  if (finderError && (filtering || readingsOpen)) {
    status = `<span data-find-status>${stateCue('unknown', 'search not read', `The components could not be read for the search: ${finderError}. The tree below is not filtered.`)} <span class="text-ink-muted">${esc(finderError)} · the tree below is not filtered</span></span>`;
  } else if (filtering && matched !== null && finder) {
    status = `<span data-find-status>${matched === 0 ? `${stateCue('nothing', 'no component matches', 'The search read every component and none matches.')} ` : ''}<span class="tnum">${matched}</span> of <span class="tnum">${total}</span> components match${cut ? ` ${stateCue('partial', `first ${cut.shown} of ${cut.total} searched`, 'The server capped the list the search reads; components past the cap are not searched.')}` : ''} · in <span class="tnum">${shownBranches}</span> of <span class="tnum">${allBranches}</span> branches
      <button type="button" data-tree-find-clear class="cursor-pointer bg-transparent p-0 text-accent-ink underline">clear</button></span>`;
  }
  return `<div data-tree-find class="mb-s1 flex flex-wrap items-baseline gap-x-s3 gap-y-[2px] text-provenance">
    <input type="search" data-tree-search value="${esc(search)}" placeholder="Find a component by name, path or type…" aria-label="Find a component"
      class="w-[30ch] rounded-sm border border-rule bg-transparent px-2 py-[1px] text-provenance text-ink">
    ${readings}${status}
  </div>`;
}

async function renderComponentTree(slug, prefix = '') {
  const host = $('component-tree');
  if (!host) { markUnloaded(slug, 'tree'); return false; }       // nowhere to draw: not loaded
  // Each render takes a token; every continuation after an await bails out if a newer render has begun, so
  // two overlapping renders never both bind handlers or both write the DOM.
  const token = host._renderToken = (host._renderToken || 0) + 1;
  const stale = () => host._renderToken !== token || !host.isConnected;
  let tree;
  try { tree = await ((!prefix && takePrefetched(slug, 'tree')) || getComponentTree(slug, prefix)); }
  catch (err) {
    if (!stale()) { host.innerHTML = `<span class="text-accent-ink">${paneError('The components could not be read: ', err, 'tree')}</span>`; markUnloaded(slug, 'tree'); }
    return false;
  }
  if (slug !== state.selectedSlug || stale()) return true;
  const me = (state.me && (state.me.user_id || state.me.username || state.me.egeria_user)) || '';
  if (!tree.branches.length) {
    host.innerHTML = `<div class="text-caveat text-ink-muted">No components recovered on this resource yet.</div>
      ${tree.topology ? `<div class="mt-s1 text-provenance text-ink-muted">${esc(tree.topology)}</div>` : ''}`;
    return;
  }
  const selected = curateSelectionSet(slug);
  const sort = state.componentSort || 'size';
  let rows = [...tree.branches];
  // The finder: search text and reading. Read only when one is asked for (or the readings are opened).
  const search = String(state.componentSearch || '');
  const readingPick = state.componentReading || '';
  const filtering = !!(search.trim() || readingPick);
  let finder = null;            // every component, once read
  let finderError = '';
  let cut = null;               // set when the server capped the list the search reads
  if (filtering || state.componentReadingsOpen) {
    try { const fr = await readFinder(slug); finder = fr.rows; cut = fr.truncated ? { shown: fr.rows.length, total: fr.total } : null; } catch (err) { finderError = err.message || 'could not be read'; }
    if (slug !== state.selectedSlug || stale()) return true;
  }
  let matchPaths = null;        // the components that pass the filter; null = no filter in force
  const matchesIn = new Map(); // branch path -> how many of them sit under it
  if (filtering && finder) {
    matchPaths = new Set(finder.filter((r) => componentMatches(r, search, readingPick)).map((r) => r.path));
    for (const b of rows) {
      let n = 0;
      for (const p of matchPaths) if (p === b.path || p.startsWith(`${b.path}/`)) n += 1;
      matchesIn.set(b.path, n);
    }
    rows = rows.filter((b) => matchesIn.get(b.path) > 0);
  }
  const findHtml = findControlsHtml({ search, readingPick, finder, finderError, filtering, matched: matchPaths ? matchPaths.size : null, shownBranches: rows.length, allBranches: tree.branches.length,
    total: tree.total_components, cut });
  // A sort, never a filter: the ⚠ count already rides on the branch, so
  // ordering by evidence puts the weakest clusters first without hiding
  // one. By size is the repository's own shape.
  //
  // Agreement RAISES a branch's effective evidence (RULING-WHAT-A-VERDICT-IS-ABOUT.md §2b) — two
  // independent extractors landing on the same path
  // is a better bet than one extractor at 90%. In a weakest-first queue
  // that means agreement must SINK a branch, the same direction lower
  // confidence already does — not outrank confidence by sorting to the
  // top. (Fixed 2026-09-20: the original comparator ran agreement
  // descending and confidence ascending against each other, so the
  // best-evidenced branches surfaced first in a queue meant to open on
  // what needs the most attention — see SORT-DIRECTION-FIX-IMPLEMENTED.md.)
  // Less agreement and lower confidence both sort first; agreement is the
  // primary key, confidence breaks ties within the same agreement count.
  if (sort === 'confidence') rows.sort((a, b) => (a.agreement_count || 0) - (b.agreement_count || 0)
    || (a.min_confidence ?? 101) - (b.min_confidence ?? 101) || b.low_confidence - a.low_confidence);
  const shown = state.componentShowAll ? rows : rows.slice(0, 8);
  // The redraw keeps the person's place: open branches keep their DOM (open groups, inner scroll) and are
  // refreshed in place below, the diagram keeps its picture until the new one arrives, and every scrolled
  // ancestor gets its position back.
  const keptBoxes = new Map();
  host.querySelectorAll('[data-branch]').forEach((br) => {
    const box = br.querySelector('[data-branch-leaves]');
    if (box && !box.hidden) keptBoxes.set(br.dataset.branch, box);
  });
  const keptDiagram = host.querySelector('#component-diagram');
  const keptTopology = host.querySelector('#component-topology');
  const scrolls = [];
  for (let el = host; el; el = el.parentElement) if (el.scrollTop > 0) scrolls.push([el, el.scrollTop]);
  const winY = typeof window !== 'undefined' ? window.scrollY : 0;
  const refocus = !!state.componentFocusSearch;
  state.componentFocusSearch = false;
  host.innerHTML = `
    <div class="mb-s1 text-provenance text-ink-muted"><span class="tnum">${tree.accepted}</span> of <span class="tnum">${tree.total_components}</span> component paths accepted ·
      <span class="tnum">${tree.reviewed}</span> with a verdict of their own · <span class="tnum">${tree.branches.length}</span> branches ·
      ports and wires read from the deployment artifacts; the diagram shows those belonging to accepted components
      ${me ? '' : ' · <span class="text-accent-ink">sign in to record a verdict</span>'}
      · sort <button data-tree-sort="size" class="cursor-pointer bg-transparent p-0 ${sort === 'size' ? 'text-ink' : 'text-accent-ink underline'}">by size</button>
      / <button data-tree-sort="confidence" class="cursor-pointer bg-transparent p-0 ${sort === 'confidence' ? 'text-ink' : 'text-accent-ink underline'}">by evidence</button></div>
    ${findHtml}
    ${selectionBarHtml(selected, shown, rows.length)}
    ${shown.map((b) => branchRowHtml(b, selected.has(b.path), branchScopeMode(slug, b), matchPaths ? matchesIn.get(b.path) : null)).join('')}
    ${!state.componentShowAll && rows.length > 8 ? `<div class="py-[5px] text-provenance"><button data-tree-more class="cursor-pointer bg-transparent p-0 text-accent-ink underline">and <span class="tnum">${rows.length - 8}</span> more branches${icon('chevron-right', { size: 12 })}</button></div>` : ''}
    ${tree.topology ? `<div class="mt-s2 text-provenance text-ink-muted">${esc(tree.topology)}</div>` : ''}
    ${tree.topology_totals ? `<div class="mt-s2 text-provenance text-ink-muted">${tnum(esc(tree.topology_totals))}</div>` : ''}
    <div id="component-tree-status" class="mt-s1 text-provenance text-ink-muted"></div>
    <div id="component-diagram" class="mt-s3"></div>
    <details id="component-topology" data-curate-ports-wires class="mt-s3"><summary class="cursor-pointer text-caps uppercase tracking-caps text-ink">Ports &amp; Wires</summary>
      <div class="mt-s1 text-provenance text-ink-muted">Declared deployment topology, read-only: there is no verdict to give here. It is not an Egeria SolutionPort or SolutionLinkingWire; those are not written yet.</div>
      <div data-topology-host class="mt-s1"></div></details>`;
  const searchEl = host.querySelector('[data-tree-search]');
  if (searchEl) {
    if (refocus) { searchEl.focus(); try { searchEl.setSelectionRange(searchEl.value.length, searchEl.value.length); } catch { /* type=search may refuse */ } }
    searchEl.addEventListener('input', () => {
      clearTimeout(host._findTimer);
      host._findTimer = setTimeout(() => {
        state.componentSearch = searchEl.value;
        state.componentFocusSearch = true;
        renderComponentTree(slug, prefix);
      }, 150);
    });
  }
  host.querySelector('[data-tree-readings-open]')?.addEventListener('click', () => { state.componentReadingsOpen = true; renderComponentTree(slug, prefix); });
  host.querySelectorAll('[data-tree-reading]').forEach((b) => b.addEventListener('click', () => { state.componentReading = b.dataset.treeReading; renderComponentTree(slug, prefix); }));
  host.querySelector('[data-tree-find-clear]')?.addEventListener('click', () => { state.componentSearch = ''; state.componentReading = ''; renderComponentTree(slug, prefix); });
  host.querySelectorAll('[data-tree-sort]').forEach((b) => b.addEventListener('click', () => { state.componentSort = b.dataset.treeSort; renderComponentTree(slug, prefix); }));
  host.querySelector('[data-tree-more]')?.addEventListener('click', () => { state.componentShowAll = true; renderComponentTree(slug, prefix); });
  host.querySelectorAll('[data-ports-open]').forEach((b) => b.addEventListener('click', () => {
    const br = tree.branches.find((x) => x.path === b.dataset.portsOpen);
    if (br) openPortsInRail(slug, br.path, br.own_ports || []);
  }));
  if (keptDiagram) $('component-diagram')?.replaceWith(keptDiagram);
  renderComponentDiagram(slug, $('component-diagram'));
  // Ports & Wires: the same renderer as the Analysis pane. The read is slow on a large repository, so it starts
  // when the section is opened, and a redraw of the tree keeps the section (and what it drew) as it was.
  if (keptTopology) $('component-topology')?.replaceWith(keptTopology);
  else {
    const topo = $('component-topology');
    topo?.addEventListener('toggle', () => {
      if (!topo.open || topo.dataset.drawn === '1') return;
      topo.dataset.drawn = '1';
      mountDeclaredTopology(topo.querySelector('[data-topology-host]'), slug, {
        fetchResults: (x) => getAnalysisResults(x, 'architecture_recovery'),
        stale: () => slug !== state.selectedSlug,
      });
    });
  }

  host.querySelectorAll('[data-branch-scope]').forEach((t) => t.addEventListener('click', () => {
    setBranchScopeMode(slug, t.dataset.path, t.dataset.branchScope);
    renderComponentTree(slug, prefix);
  }));
  host.querySelectorAll('[data-branch-select]').forEach((c) => c.addEventListener('change', () => {
    if (c.checked) selected.add(c.dataset.branchSelect); else selected.delete(c.dataset.branchSelect);
    renderComponentTree(slug, prefix);
  }));
  host.querySelector('[data-select-all-shown]')?.addEventListener('change', (ev) => {
    shown.forEach((b) => { if (ev.target.checked) selected.add(b.path); else selected.delete(b.path); });
    renderComponentTree(slug, prefix);
  });
  // "select all matching" acts on the FULL set at this level (`rows`), not
  // just the 8 shown by default -- and the button already named the total
  // before this click, so the act does not surprise (rule 4).
  host.querySelector('[data-select-all-matching]')?.addEventListener('click', () => {
    rows.forEach((b) => selected.add(b.path));
    renderComponentTree(slug, prefix);
  });
  host.querySelector('[data-selection-clear]')?.addEventListener('click', () => { selected.clear(); renderComponentTree(slug, prefix); });
  host.querySelector('[data-selection-verdict="accepted"]')?.addEventListener('click', () => {
    const paths = [...selected];
    const opts = tree.branches.filter((b) => paths.includes(b.path)).map((b) => scopedVerdictOptions(slug, b));
    recordVerdicts(slug, paths, 'accepted', {
      count: opts.reduce((n, o) => n + o.count, 0),
      low: opts.reduce((n, o) => n + o.low, 0),
      exists: opts.reduce((n, o) => n + o.exists, 0),
      onlyScopes: opts.flatMap((o) => o.onlyScopes),
    }, () => { selected.clear(); }, host.querySelector('[data-selection-verdict="accepted"]'));
  });
  host.querySelector('[data-selection-verdict="rejected"]')?.addEventListener('click', () => {
    const opts = tree.branches.filter((b) => selected.has(b.path)).map((b) => scopedVerdictOptions(slug, b));
    recordVerdicts(slug, [...selected], 'rejected', { count: 0, low: 0, onlyScopes: opts.flatMap((o) => o.onlyScopes) },
    () => { selected.clear(); }, host.querySelector('[data-selection-verdict="rejected"]'));
  });

  /** Reads a branch's leaves into its box. `refresh` keeps what is on screen (and each group's open state)
   *  until the new rows arrive, so a verdict does not collapse the place the person is working in.
   *  A per-box sequence number discards an older response; open state is read when the rows are applied. */
  const loadLeaves = async (path, box, { refresh = false } = {}) => {
    const mine = box._leafSeq = (box._leafSeq || 0) + 1;
    if (!refresh) { box.hidden = false; box.innerHTML = `<span class="text-provenance text-ink-muted">reading…</span>`; }
    try {
      const out = await getComponentLeaves(slug, path);
      if (box._leafSeq !== mine || !box.isConnected) return;
      const openByName = new Map();
      box.querySelectorAll('details[data-leaf-group]').forEach((d) => openByName.set(d.dataset.leafGroup, d.open));
      // Grouped by scope-hierarchy cluster when the backend found groups worth having
      // (`group_leaves`'s own MIN_GROUP=2 rule); ungrouped leaves render plainly; a branch with no
      // groups at all falls back to the flat list.
      // A search or a reading in force narrows the rows to the components that pass it; the box says how many
      // of the branch's components that is, so a narrowed list is never mistaken for the whole branch.
      const keepRow = (l) => !matchPaths || matchPaths.has(l.path);
      const leafMode = (l) => branchScopeMode(slug, leafAsBranch(l));
      const verdictOf = (l) => (l.verdict || {}).verdict;
      const groups = (out.groups || []).map((g) => {
        if (!matchPaths) return g;
        const members = g.members.filter(keepRow);
        return { ...g, members, accepted: members.filter((l) => verdictOf(l) === 'accepted').length,
          rejected: members.filter((l) => verdictOf(l) === 'rejected').length,
          undecided: members.filter((l) => !['accepted', 'rejected'].includes(verdictOf(l))).length };
      }).filter((g) => g.members.length);
      const ungrouped = (out.ungrouped || out.leaves).filter(keepRow);
      const innerScroll = box.scrollTop;
      const narrowed = matchPaths ? out.leaves.filter(keepRow).length : null;
      box.innerHTML = (narrowed === null ? '' : `<div data-leaf-narrowed class="pb-[2px] text-provenance text-ink-muted">${
        stateCue('partial', 'narrowed', 'A search or reading is in force; the rows below are the components that pass it.')} <span class="tnum">${narrowed}</span> of <span class="tnum">${out.leaves.length}</span> components under this branch</div>`)
        + (groups.map((g) => leafGroupHtml(g, openByName, leafMode)).join('') + ungrouped.map((l) => leafRowHtml(l, leafMode(l))).join(''))
        || `<span class="text-provenance text-ink-muted">nothing under this branch</span>`;
      box.querySelectorAll('[data-leaf-blueprints]').forEach((bb) => bb.addEventListener('click', () => showLeafBlueprints(slug, bb)));
      box.scrollTop = innerScroll;
      box.querySelectorAll('[data-leaf-verdict]').forEach((lb) => lb.addEventListener('click', () => {
        // A leaf with components beneath it presses like a branch row (its choice, its count); one alone is one.
        const l = out.leaves.find((x) => x.path === lb.dataset.scope);
        const opts = l && (l.children || 0) > 0 ? scopedVerdictOptions(slug, leafAsBranch(l)) : { count: 1, low: 0 };
        recordVerdicts(slug, [lb.dataset.scope], lb.dataset.leafVerdict, opts, undefined, lb);
      }));
      box.querySelectorAll('[data-leaf-scope]').forEach((t) => t.addEventListener('click', () => {
        setBranchScopeMode(slug, t.dataset.path, t.dataset.leafScope);
        loadLeaves(path, box, { refresh: true });
      }));
      // Accept all / reject all for a whole group (a scope-hierarchy cluster such as compose-configs/optional-...).
      // Only the members with no verdict of their own are posted; the confirm says exactly that number.
      box.querySelectorAll('[data-group-verdict]').forEach((gb) => gb.addEventListener('click', () => {
        // The undecided set is read from the rows on screen at PRESS time (never from a closure over an earlier
        // read), and a batch in flight for this box is not started twice.
        // One batch per GROUP: a press on a second group of the same branch is not blocked by the first.
        const busy = box._groupBusy = box._groupBusy || {};
        const gname = gb.dataset.group;
        if (busy[gname]) return;
        const group = gb.closest('details[data-leaf-group]');
        const rowsNow = group ? [...group.querySelectorAll('[data-leaf-path]')] : [];
        const todo = rowsNow.filter((r) => r.dataset.leafUndecided === '1').map((r) => r.dataset.leafPath);
        if (!todo.length) return;
        const verdict = gb.dataset.groupVerdict;
        const known = new Map(((groups.find((x) => x.name === gb.dataset.group) || {}).members || []).map((m) => [m.path, m]));
        recordVerdicts(slug, todo, verdict, {
          count: todo.length, low: todo.filter((pth) => (known.get(pth) || {}).low_confidence).length, exists: 0, confirmAlways: true,
          onStart: () => { busy[gname] = true; },
          onSettled: () => { busy[gname] = false; },
          // after a failed batch: how many of the batch now carry the verdict (read fresh from the server)
          countRecorded: async () => {
            const again = await getComponentLeaves(slug, path);
            const now = new Map((again.leaves || []).map((l) => [l.path, l]));
            return todo.filter((pth) => { const v = (now.get(pth) || {}).verdict; return v && v.verdict === verdict && !v.inherited_from; }).length;
          },
        }, () => {
          // the posted rows are decided now, before the refresh lands: a quick second press finds nothing left
          todo.forEach((pth) => { const r = group && group.querySelector(`[data-leaf-path="${CSS.escape(pth)}"]`); if (r) r.dataset.leafUndecided = '0'; });
        }, gb);
      }));
      box.querySelectorAll('[data-ports-open]').forEach((pb) => pb.addEventListener('click', () => {
        const leaf = out.leaves.find((x) => x.path === pb.dataset.portsOpen);
        if (leaf) openPortsInRail(slug, leaf.path, leaf.ports || []);
      }));
    } catch (err) {
      if (box._leafSeq !== mine) return;
      if (!refresh) { box.innerHTML = `<span class="text-provenance text-accent-ink">could not read: ${esc(err.message)}</span>`; return; }
      // The rows on screen may be out of date: say so on the box, faintly, with the sentence on demand.
      box.querySelector('[data-refresh-failed]')?.remove();
      box.insertAdjacentHTML('afterbegin', `<div data-refresh-failed class="text-provenance">${
        stateCue('error', 'could not refresh', 'This branch could not be re-read after the change, so the rows below may be out of date. Reload to see the current state.')}</div>`);
    }
  };
  host.querySelectorAll('[data-branch-open]').forEach((b) => b.addEventListener('click', async () => {
    const box = host.querySelector(`[data-branch="${CSS.escape(b.dataset.branchOpen)}"] [data-branch-leaves]`);
    if (!box) return;
    if (!box.hidden) { box.hidden = true; return; }
    await loadLeaves(b.dataset.branchOpen, box);
  }));
  host.querySelectorAll('[data-branch-verdict]').forEach((b) => b.addEventListener('click', () => {
    const br = tree.branches.find((x) => x.path === b.dataset.scope);
    const { count, low, exists, onlyScopes } = br ? scopedVerdictOptions(slug, br) : { count: 0, low: 0, exists: 0, onlyScopes: [] };
    recordVerdicts(slug, [b.dataset.scope], b.dataset.branchVerdict, { count, low, exists, onlyScopes }, undefined, b);
  }));
  // Put the open branches back as they were, then refresh them in place from a re-read.
  const refreshes = [];
  for (const [path, oldBox] of keptBoxes) {
    const fresh = host.querySelector(`[data-branch="${CSS.escape(path)}"] [data-branch-leaves]`);
    if (!fresh) continue;
    fresh.replaceWith(oldBox);
    refreshes.push(loadLeaves(path, oldBox, { refresh: true }));
  }
  scrolls.forEach(([el, top]) => { el.scrollTop = top; });
  if (winY && typeof window !== 'undefined') window.scrollTo(0, winY);
  // After the refreshes land, restore the saved position only if the person has not scrolled since.
  const placed = scrolls.map(([el]) => el.scrollTop);
  await Promise.all(refreshes);
  if (!stale()) scrolls.forEach(([el, top], i) => { if (el.scrollTop === placed[i]) el.scrollTop = top; });
}

/* ── Blueprints ───────────────────────────────────────────────────────────
 *
 * SPEC-CURATE-SELECTION-AND-BLUEPRINTS.md §2/§3/§4. A candidate blueprint is
 * clustering.py's proposal that a group of components forms a cohesive unit;
 * accepting one materialises a real Egeria SolutionBlueprint
 * (blueprint_materializer.py). "0 of 12 clusters in the logical reading
 * reviewed" was already the coverage sentence on this pane -- this is the
 * screen that count opens onto, since a count that opens nothing is the one
 * thing this app does not do.
 *
 * A cluster is keyed `perspective::cluster_name` and exists in exactly one
 * READING (RULING-WHAT-A-VERDICT-IS-ABOUT.md §0 renamed `Component.
 * perspective` to "reading" precisely so this would not read as the same
 * axis as the diagram's "found by" or the chrome's Perspective filter -- all
 * three used to share the one word "perspective"). So this list is scoped to
 * ONE reading at a time, says so at its head, and switching readings
 * REPLACES the list outright rather than diffing it against the last one. */

/** The blueprint selector at the top of "what it's made of" (brief section 4): one row per KIND RE can
 *  offer, with its source and its state. The server says what is drawn; a kind that is not drawn is
 *  listed as "not yet drawn" and offers no view, never a button that opens nothing. "Write to Egeria"
 *  is per blueprint (the accept on each blueprint row), never per kind. */
export function blueprintSelectorHtml(kinds, reading) {
  if (!kinds || !kinds.length) return '';
  const cue = (k) => (k.state === 'accepted' ? stateCue('measured', 'accepted')
    : k.state === 'proposed' ? stateCue('proposal', 'proposed') : stateCue('unrun', 'not yet drawn'));
  return `<div data-blueprint-selector class="mb-s2 border-b border-rule pb-s1">
    <div class="mb-[2px] text-caps uppercase tracking-caps text-ink-muted">Blueprints this repository can be read as</div>
    ${kinds.map((k) => `<div data-blueprint-kind="${esc(k.kind)}" class="flex flex-wrap items-baseline gap-x-s2 py-[2px] text-caveat">
      ${k.drawn
        ? `<button type="button" data-blueprint-view="${esc(k.perspective)}" aria-pressed="${k.perspective === reading ? 'true' : 'false'}"
             class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[1px] text-ink">${k.perspective === reading ? '● viewing' : 'view'}</button>`
        : `<span class="px-2 py-[1px] text-ink-muted">○</span>`}
      <span class="text-ink">${esc(k.name)}</span>
      <span class="text-provenance text-ink-muted">· ${esc(k.source)}</span>
      <span class="text-provenance">· ${cue(k)}</span>
    </div>`).join('')}
  </div>`;
}

/** The blueprint's own verdict, rendered the same shape as a component's
 *  `verdictBadge` -- but a blueprint verdict never inherits (it has no
 *  ancestor scope the way a path does) and has no "retyped" outcome
 *  (BLUEPRINT_VERDICTS has no equivalent free-text field to correct). */
function blueprintVerdictBadge(v) {
  if (!v) return `<span class="text-ink-muted">undecided</span>`;
  return `<span class="text-ink">${esc(v.verdict)}</span>${v.decided_at ? ` <span class="text-ink-muted">· ${esc(ago(v.decided_at))}</span>` : ''}`;
}

/** SPEC §4, the hard requirement: accepting a blueprint materialises the
 *  SolutionBlueprint element itself, but blueprint_materializer.py does NOT
 *  attach members as a synchronous part of that write -- workflows/curate.py's
 *  materialize_blueprint_if_accepted queues them onto the outbox instead
 *  (egeria_outbox.enqueue_blueprint_members), which drains later, on its own
 *  schedule, and this pane has no record of whether a given queue row has
 *  actually landed as a real CollectionMembership by the time anyone reads
 *  this screen again. So the honest claim is narrower than "linked" and
 *  narrower than "not built" both: not-yet-confirmed-linked, counted.
 *
 *  "Accepted component" here means a member/child that itself has a
 *  materialized Egeria element (`member_status[].materialized`) -- the same
 *  fact `resolve_member_guids` requires before it will even attempt to
 *  enqueue that member's attachment (Decision 2: accepting a blueprint does
 *  NOT implicitly accept or materialize its members). A member with no
 *  verdict of its own, or an accepted-but-unmaterialized one, is not counted
 *  here -- it was never a membership candidate in the first place. */
function membershipHonestyLine(bp) {
  const materializedMembers = (bp.member_status || []).filter((m) => m.materialized).length;
  const materializedChildren = (bp.child_status || []).filter((c) => c.materialized).length;
  const total = materializedMembers + materializedChildren;
  if (!total) {
    return `<div class="text-caveat text-ink-muted">its members are not yet linked — none of its proposed members are in Egeria as their own elements yet, so there is nothing to link</div>`;
  }
  const parts = [];
  if (materializedMembers) parts.push(`<span class="tnum">${materializedMembers}</span> accepted component${materializedMembers === 1 ? '' : 's'}`);
  if (materializedChildren) parts.push(`<span class="tnum">${materializedChildren}</span> child blueprint${materializedChildren === 1 ? '' : 's'}`);
  return `<div class="text-caveat text-ink-muted">its members are not yet confirmed linked — <button data-blueprint-standapart="${esc(bp.perspective)}::${esc(bp.cluster_name)}"
    class="cursor-pointer bg-transparent p-0 text-accent-ink underline">${parts.join(' and ')} stand apart${icon('chevron-right', { size: 12 })}</button></div>`;
}

/** Accepting is per blueprint and only for one with accepted nodes (brief section 4): a blueprint none of
 *  whose components or child blueprints has been accepted would be written empty when it is published.
 *  Without an accepted node the control is replaced by the reason, in a short word. */
export function blueprintWriteHtml(bp, accepted) {
  const nodes = (bp.member_status || []).filter((m) => (m.verdict || {}).verdict === 'accepted').length
    + (bp.child_status || []).filter((c) => (c.verdict || {}).verdict === 'accepted').length;
  if (!nodes && !accepted) {
    return `<span data-blueprint-write-blocked class="text-ink-muted" title="A blueprint is published to Egeria only when at least one of its components or child blueprints is accepted.">no accepted component · nothing to publish</span>`;
  }
  return `<button data-blueprint-verdict="accepted" data-key="${esc(bp.perspective)}::${esc(bp.cluster_name)}" class="cursor-pointer bg-transparent p-0 text-accent-ink underline">${accepted ? 'change' : 'accept'}</button>`;
}

export function blueprintRowHtml(bp) {
  const v = bp.verdict;
  const accepted = v?.verdict === 'accepted';
  const rejected = v?.verdict === 'rejected';
  const memberCount = (bp.members || []).length;
  const acceptedMembers = (bp.member_status || []).filter((m) => (m.verdict || {}).verdict === 'accepted').length;
  const why = [bp.signal, bp.carrier].filter(Boolean).join(' · ');
  return `<div class="border-b border-rule py-s2" data-blueprint="${esc(bp.perspective)}::${esc(bp.cluster_name)}">
    <div class="flex flex-wrap items-baseline gap-x-s2 gap-y-[2px]">
      <span class="font-mono text-answer text-ink">${esc(bp.cluster_name)}</span>
      ${bp.oversized ? `<span class="text-provenance text-state-warn">· ⚠ oversized (target <span class="tnum">${bp.target_size ?? '?'}</span>)</span>` : ''}
      ${why ? `<span class="text-provenance text-ink-muted">· ${esc(why)}</span>` : ''}
      <span class="text-provenance text-ink-muted">· <button data-blueprint-members="${esc(bp.perspective)}::${esc(bp.cluster_name)}"
        class="cursor-pointer bg-transparent p-0 text-accent-ink underline"><span class="tnum">${acceptedMembers}</span> of <span class="tnum">${memberCount}</span> members accepted${icon('chevron-right', { size: 12 })}</button></span>
    </div>
    <div class="mt-[2px] flex flex-wrap items-baseline gap-x-s3 text-provenance">
      <span>${blueprintVerdictBadge(v)}</span>
      ${promotionHtml(bp.promotion)}
      ${blueprintWriteHtml(bp, accepted)}
      <button data-blueprint-verdict="rejected" data-key="${esc(bp.perspective)}::${esc(bp.cluster_name)}" class="cursor-pointer bg-transparent p-0 text-ink-muted underline">reject</button>
    </div>
    ${accepted
      ? (bp.materialized
          ? `<div class="mt-[2px] text-caveat text-ink" data-in-egeria="yes">${stateCue('measured', 'in Egeria')} Solution Blueprint · <span class="font-mono">${esc((bp.materialized.guid || '').slice(0, 8))}…</span></div>
             ${membershipHonestyLine(bp)}`
          : `<div class="mt-[2px] text-caveat" data-in-egeria="no">${stateCue('unrun', 'accepted · not in Egeria yet', 'Accepting is a decision. Nothing is written to Egeria until Publish is pressed.')}</div>`)
      : rejected
        ? (bp.materialized
          ? `<div class="mt-[2px] text-caveat" data-in-egeria="still">${stateCue('partial', 'rejected · still in Egeria', 'Rejecting writes nothing: Resource Explorer cannot remove an element from Egeria, so the blueprint stays there until a steward removes it.')}</div>`
          : `<div class="mt-[2px] text-caveat text-ink-muted">rejected · nothing in Egeria</div>`)
        : ''}
  </div>`;
}

/** The rail: one cluster's members/children, each with its own verdict and
 *  materialization state -- what "N members" or "N stand apart" opens onto,
 *  the same "a count opens what it counted" rule every other rail here
 *  follows. */
function openBlueprintMembersInRail(slug, bp, { standApartOnly = false } = {}) {
  ensureRailShowing();
  railClaim();
  let members = (bp.member_status || []).map((m) => ({ ...m, kind: 'component' }));
  let children = (bp.child_status || []).map((c) => ({ ...c, kind: 'blueprint', slug: c.cluster_name }));
  if (standApartOnly) {
    members = members.filter((m) => m.materialized);
    children = children.filter((c) => c.materialized);
  }
  const rows = [...members, ...children];
  const body = railFrame('Members', slug, `
    <div class="mb-s1 text-caps text-chrome-muted">${esc(bp.cluster_name)} · ${esc(bp.perspective)} reading${standApartOnly ? ' · in Egeria but not confirmed linked to the blueprint' : ''}</div>
    ${rows.length ? rows.map((m, k) => `<div data-member-row="${k}" class="flex flex-wrap items-baseline gap-s2 border-b border-chrome-line-soft py-[3px] text-caps">
      <span class="font-mono text-chrome-ink">${esc(m.slug)}</span>
      <span class="text-chrome-muted">${m.kind === 'blueprint' ? 'child blueprint' : 'component'}</span>
      <span class="text-chrome-muted">${m.verdict ? esc(m.verdict.verdict) : 'undecided'}</span>
      <span class="text-chrome-muted">${m.materialized ? 'in Egeria' : 'not in Egeria yet'}</span>
      ${m.kind === 'blueprint'
        ? `<button type="button" data-jump-member="${k}" title="Show this blueprint in the list" class="cursor-pointer bg-transparent p-0 text-caps text-accent-on-dark underline">show blueprint${icon('chevron-right', { size: 12 })}</button>`
        : m.scope_locator
          ? `<button type="button" data-jump-member="${k}" title="Show this component in the tree" class="cursor-pointer bg-transparent p-0 text-caps text-accent-on-dark underline">show in tree${icon('chevron-right', { size: 12 })}</button>`
          : ''}
    </div>`).join('') : `<div class="text-caps text-chrome-muted">nothing to show</div>`}`,
    { sub: `${rows.length} of ${(bp.member_status || []).length + (bp.child_status || []).length}` });
  body?.querySelectorAll('[data-jump-member]').forEach((b) => b.addEventListener('click', () => {
    const m = rows[Number(b.dataset.jumpMember)];
    if (!m) return;
    if (m.kind === 'blueprint') showBlueprintInList(slug, bp.perspective, m.cluster_name, b);
    else showComponentInTree(slug, m.scope_locator, b);
  }));
}

/* ── Jumping between a blueprint and its components, both ways (PI-071) ──────
 * Both directions read the SAME rows the lists draw from (the blueprints read's `member_status`), and a
 * target that cannot be found says so next to the control that was pressed. */
const waitFor = async (find, ms = 3000) => {
  const t0 = Date.now();
  for (;;) {
    const v = find();
    if (v) return v;
    if (Date.now() - t0 > ms) return null;
    await new Promise((r) => setTimeout(r, 40));
  }
};
function flash(el) {
  el.dataset.jumped = '1';
  el.classList.add('bg-accent-tint');
  el.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
  setTimeout(() => { el.classList.remove('bg-accent-tint'); delete el.dataset.jumped; }, 2400);
}
function jumpNote(btn, text) {
  if (!btn || !btn.parentElement) return;
  btn.parentElement.querySelector('[data-jump-note]')?.remove();
  btn.insertAdjacentHTML('afterend', btn.closest('#rail-evidence')
    ? ` <span data-jump-note class="text-state-warn-on-dark">${esc(text)}</span>`
    : ` <span data-jump-note class="text-state-warn">${esc(text)}</span>`);
}
/** Open a Curate section and wait for what it reads. */
async function openCurateSection(id) {
  const el = document.getElementById(id);
  if (!el) return false;
  if (el.tagName === 'DETAILS' && !el.open) { el.open = true; rememberSection(id, true); }
  const loading = state.curate && state.curate.loadSection ? state.curate.loadSection(id) : null;
  if (loading) await loading;
  return true;
}

/** Blueprint -> component: open "what it's made of", open the branch that holds the component, show the row. */
export async function showComponentInTree(slug, path, pressed = null) {
  if (!(await openCurateSection('curate-sec-made-of'))) { jumpNote(pressed, 'the components section is not on this page'); return false; }
  const host = $('component-tree');
  // A filter would hide the row: clear it, and draw the tree again.
  const filtered = !!(state.componentSearch || state.componentReading);
  if (filtered) { state.componentSearch = ''; state.componentReading = ''; await renderComponentTree(slug); }
  const top = String(path).split('/')[0];
  const findBranch = () => [...(host?.querySelectorAll('[data-branch]') || [])].find((b) => b.dataset.branch === top);
  let branch = findBranch();
  if (!branch && !state.componentShowAll) { state.componentShowAll = true; await renderComponentTree(slug); branch = findBranch(); }
  if (!branch) { jumpNote(pressed, 'not in the component tree: this survey no longer proposes it'); return false; }
  const findLeaf = () => [...branch.querySelectorAll('[data-leaf-path]')].find((l) => l.dataset.leafPath === path);
  if (!findLeaf()) {
    const box = branch.querySelector('[data-branch-leaves]');
    if (box && box.hidden) branch.querySelector('[data-branch-open]')?.click();
  }
  const leaf = await waitFor(findLeaf);
  if (!leaf) { jumpNote(pressed, 'not under its branch: this survey no longer proposes it'); return false; }
  const grp = leaf.closest('details[data-leaf-group]');
  if (grp) grp.open = true;
  flash(leaf);
  return true;
}

/** Blueprint (or a component's blueprint) -> the blueprint row: open "blueprints", switch to its reading, show it. */
export async function showBlueprintInList(slug, perspective, clusterName, pressed = null) {
  if (!(await openCurateSection('curate-sec-blueprints'))) { jumpNote(pressed, 'the blueprints section is not on this page'); return false; }
  const rk = blueprintReadingKey(slug);
  const key = `${perspective}::${clusterName}`;
  const find = () => [...document.querySelectorAll('[data-blueprint]')].find((b) => b.dataset.blueprint === key);
  if (rk.reading !== perspective || !find()) {
    rk.reading = perspective;
    state.blueprintShowAll = true;                  // the cluster may sit past the first page
    await renderBlueprintList(slug);
  }
  const row = find();
  if (!row) { jumpNote(pressed, 'not in this survey\'s clusters'); return false; }
  flash(row);
  return true;
}

/** Component -> blueprints: which candidate blueprints list this component as a member, read from the
 *  blueprints read the list itself uses. None is a measured answer; a failed read is not "none". */
async function showLeafBlueprints(slug, btn) {
  const path = btn.dataset.leafBlueprints;
  const line = btn.closest('[data-leaf-path]')?.querySelector('[data-leaf-bp-line]');
  if (!line) return;
  line.innerHTML = stateCue('running', 'reading blueprints');
  let data;
  try { data = await getComponentBlueprints(slug); }
  catch (err) { line.innerHTML = `${stateCue('unknown', 'blueprints not read', err.message)} <span class="text-ink-muted">${esc(err.message)}</span>`; return; }
  const mine = (data.blueprints || []).filter((bp) => (bp.member_status || []).some((m) => m.scope_locator === path));
  if (!mine.length) {
    line.innerHTML = `${stateCue('nothing', 'in no candidate blueprint', 'The blueprints read lists this component under no cluster.')}`;
    return;
  }
  line.innerHTML = `<span class="text-ink-muted">member of</span> ${mine.map((bp, k) => `<button type="button" data-leaf-bp="${k}"
    class="cursor-pointer bg-transparent p-0 text-accent-ink underline">${esc(bp.cluster_name)} <span class="text-ink-muted no-underline">(${esc(bp.perspective)} reading)</span>${icon('chevron-right', { size: 12 })}</button>`).join(' · ')}`;
  line.querySelectorAll('[data-leaf-bp]').forEach((b) => b.addEventListener('click', () => {
    const bp = mine[Number(b.dataset.leafBp)];
    showBlueprintInList(slug, bp.perspective, bp.cluster_name, b);
  }));
}

function blueprintReadingKey(slug) {
  if (!state.blueprintReading || state.blueprintReading.slug !== slug) {
    state.blueprintReading = { slug, reading: null };
  }
  return state.blueprintReading;
}

async function renderBlueprintList(slug) {
  const host = $('blueprint-list');
  if (!host) { markUnloaded(slug, 'blueprints'); return false; }   // nowhere to draw: not loaded
  // Each render takes a token; an older read finishing after a newer one began must not paint into its slot.
  const token = host._renderToken = (host._renderToken || 0) + 1;
  const stale = () => host._renderToken !== token || !host.isConnected;
  let data;
  try { data = await (takePrefetched(slug, 'blueprints') || getComponentBlueprints(slug)); }
  catch (err) {
    if (!stale()) { host.innerHTML = `<span class="text-accent-ink">${paneError('The blueprints could not be read: ', err, 'blueprints')}</span>`; markUnloaded(slug, 'blueprints'); }
    return false;
  }
  if (slug !== state.selectedSlug || stale()) return true;
  const { blueprints, perspectives } = data;
  const selectorSlot = $('blueprint-selector');
  // What the commit table counts: blueprint VERDICTS on record (curate_plan.py, `blueprints_accepted`), which
  // outlive a re-survey. The band lists CLUSTERS the latest survey proposes. The two are different things, so
  // the band says what each is, and when it shows none it still says something.
  const vc = (state.curate && state.curate.blueprintCounts) || {};
  const onRecord = vc.blueprints_accepted || 0;
  const reviewed = vc.blueprints_reviewed || 0;
  const recordLine = onRecord || reviewed
    ? `<span class="tnum">${onRecord}</span> accepted blueprint verdict${onRecord === 1 ? '' : 's'} on record (of <span class="tnum">${reviewed}</span> reviewed); verdicts are recorded on clusters from earlier surveys and kept, and the commit table counts them`
    : 'no blueprint verdicts on record either';
  if (!perspectives.length) {
    if (selectorSlot) selectorSlot.innerHTML = blueprintSelectorHtml(data.kinds, '') + admissionNoteHtml(data.admission);
    host.innerHTML = `<div data-blueprints-empty class="text-caveat text-ink">No candidate blueprints in this survey.
      <span class="text-ink-muted">${recordLine}${onRecord ? '; this survey proposes no cluster for them to attach to' : ''}.</span></div>`;
    return;
  }
  const shownAccepted = blueprints.filter((bp) => bp.verdict?.verdict === 'accepted').length;
  const recordFooter = onRecord
    ? ` · <span data-blueprint-record-line><span class="tnum">${shownAccepted}</span> of the <span class="tnum">${onRecord}</span> accepted blueprint verdicts on record belong to a cluster shown here${
      shownAccepted === onRecord ? '' : '; the rest were recorded on clusters this survey no longer proposes'}</span>` : '';
  const rk = blueprintReadingKey(slug);
  if (!rk.reading || !perspectives.includes(rk.reading)) rk.reading = perspectives[0];
  const reading = rk.reading;
  if (selectorSlot) {
    selectorSlot.innerHTML = blueprintSelectorHtml(data.kinds, reading) + admissionNoteHtml(data.admission);
    selectorSlot.querySelectorAll('[data-blueprint-view]').forEach((b) => b.addEventListener('click', () => {
      rk.reading = b.dataset.blueprintView;       // an immediate visible change: the pressed row reads "viewing"
      renderBlueprintList(slug);
    }));
  }
  const inReading = blueprints.filter((bp) => bp.perspective === reading);
  const others = perspectives.filter((p) => p !== reading)
    .map((p) => ({ p, n: blueprints.filter((bp) => bp.perspective === p).length }));
  // §3: replaced outright on every render, never diffed against the
  // previous reading's rows -- this function is always called with a fresh
  // innerHTML assignment, so there is no patch step to accidentally add.
  // The section wrapper is the ONE heading (it owns the id and the collapse); here is only a muted line.
  // A long list is capped like the component tree's branches: the first page plus anything that needs a
  // person (an undecided cluster with a warning), and "and N more clusters ›" for the rest.
  const PAGE = 10;
  const needsAttention = (bp) => !bp.verdict && !!bp.oversized;
  const shownRows = state.blueprintShowAll ? inReading : inReading.filter((bp, i) => i < PAGE || needsAttention(bp));
  const hiddenN = inReading.length - shownRows.length;
  host.innerHTML = `
    <p data-blueprint-reading-line class="mb-s1 text-provenance text-ink-muted">candidate clusters, in the ${esc(reading)} reading</p>
    <p class="mb-s2 max-w-[70ch] text-caveat text-ink-muted">A verdict here is recorded against <span class="font-mono">${esc(reading)}::cluster name</span>
      and applies in this reading only — switching readings shows a different set, not the same set re-judged.</p>
    ${inReading.length ? shownRows.map(blueprintRowHtml).join('') : `<div class="text-caveat text-ink-muted">No candidate blueprints proposed in the ${esc(reading)} reading.</div>`}
    <div class="mt-s2 text-provenance text-ink-muted"><span class="tnum">${shownRows.length}</span> of <span class="tnum">${inReading.length}</span> clusters shown${
      hiddenN ? ` · <button type="button" data-blueprint-more class="cursor-pointer bg-transparent p-0 text-accent-ink underline">and <span class="tnum">${hiddenN}</span> more cluster${hiddenN === 1 ? '' : 's'}${icon('chevron-right', { size: 12 })}</button>` : ''} · all in the <span class="text-ink">${esc(reading)}</span> reading${recordFooter}
      ${others.map((o) => ` · <button data-blueprint-reading="${esc(o.p)}" class="cursor-pointer bg-transparent p-0 text-accent-ink underline">the ${esc(o.p)} reading has <span class="tnum">${o.n}</span>${icon('chevron-right', { size: 12 })}</button>`).join('')}</div>
    <div id="blueprint-status" class="mt-s1 text-provenance text-ink-muted"></div>`;

  host.querySelector('[data-blueprint-more]')?.addEventListener('click', () => { state.blueprintShowAll = true; renderBlueprintList(slug); });
  host.querySelectorAll('[data-blueprint-reading]').forEach((b) => b.addEventListener('click', () => {
    rk.reading = b.dataset.blueprintReading;
    renderBlueprintList(slug);
  }));
  host.querySelectorAll('[data-blueprint-members]').forEach((b) => b.addEventListener('click', () => {
    const bp = inReading.find((x) => `${x.perspective}::${x.cluster_name}` === b.dataset.blueprintMembers);
    if (bp) openBlueprintMembersInRail(slug, bp);
  }));
  host.querySelectorAll('[data-blueprint-standapart]').forEach((b) => b.addEventListener('click', () => {
    const bp = inReading.find((x) => `${x.perspective}::${x.cluster_name}` === b.dataset.blueprintStandapart);
    if (bp) openBlueprintMembersInRail(slug, bp, { standApartOnly: true });
  }));
  host.querySelectorAll('[data-blueprint-verdict]').forEach((b) => b.addEventListener('click', () => {
    const bp = inReading.find((x) => `${x.perspective}::${x.cluster_name}` === b.dataset.key);
    if (bp) recordBlueprintVerdict(slug, bp, b.dataset.blueprintVerdict, b);
  }));
}

/** Accepting a cluster is a decision: it records that the SolutionBlueprint is wanted, with the shape and
 *  identifier chosen here, and nothing is written to Egeria until Publish. Rejecting writes nothing either,
 *  so it records at once. */
function recordBlueprintVerdict(slug, bp, verdict, pressedEl = null) {
  const status = $('blueprint-status');
  let chosenShape = '';   // a person's flip of the shape; '' takes the default the plan names
  let chosenIdentifier = '';   // a person's identifier, asked for only for a second blueprint of the kind
  const go = async () => {
    if (pressedEl && pressedEl.dataset.phase === 'pending') return;
    pressPhase(pressedEl, 'pending', verdict === 'accepted' ? 'accepting…' : 'rejecting…');
    if (status) status.innerHTML = stateCue('running', 'recording…');
    try {
      await postBlueprintVerdict(slug, bp.perspective, bp.cluster_name, verdict, '', chosenShape, chosenIdentifier);
      pressPhase(pressedEl, 'done', verdict === 'accepted' ? 'accepted' : 'rejected');
      document.dispatchEvent(new CustomEvent(ARCHITECTURE_CHANGED, { detail: { slug } }));   // Publish re-reads its list
      await renderBlueprintList(slug);
    } catch (err) {
      const why = err.status === 401 ? 'sign in to record a verdict' : err.status === 403 ? `not permitted · ${err.message}` : err.message;
      pressPhase(pressedEl, 'error', 'failed · press to retry', why);
      if (pressedEl) pressedEl.disabled = false;
      if (status) status.innerHTML = `<span class="text-accent-ink">not recorded — ${esc(why)}</span>`;
    }
  };
  if (verdict !== 'accepted') { go(); return; }
  const el = openDialog('Accept a blueprint', `${bp.perspective}::${bp.cluster_name}`);
  const body = el.querySelector('#wl-detail-body');
  const memberCount = (bp.members || []).length;
  body.innerHTML = `
    <p class="text-caveat text-ink">Records that <span class="font-mono">${esc(bp.cluster_name)}</span> should be a real Egeria <span class="font-mono">SolutionBlueprint</span> —
      the type is pinned (SPEC-CURATE-SELECTION-AND-BLUEPRINTS.md §0), unlike an individual component's. Nothing is written to Egeria until you press Publish.</p>
    <p class="text-caveat text-ink-muted">${memberCount ? `<span class="tnum">${memberCount}</span> proposed member${memberCount === 1 ? '' : 's'}, but this does not accept or publish them —
      only members already accepted and in Egeria get linked when the blueprint is published, and that link is confirmed by a read afterwards.` : 'This cluster has no proposed members.'}</p>
    ${shapeManifestHtml(bp.shape_plan)}
    ${identifierBoxHtml(bp.identity)}
    <p class="text-caveat text-ink-muted">A verdict is a new row; changing it later is another row, and the trail keeps both.</p>
    <div class="mt-s3 flex gap-s3">
      <button data-act="confirm" class="cursor-pointer rounded-sm border border-accent bg-transparent px-3 py-[3px] text-answer text-accent-ink">Accept</button>
      <button data-act="close" class="cursor-pointer bg-transparent p-0 text-provenance text-ink-muted underline">not now</button>
    </div>`;
  const identifierBox = wireIdentifier(body, bp.identity);
  body.querySelector('[data-act="confirm"]').addEventListener('click', () => {
    // A second blueprint of a kind needs a person's identifier: not given or not valid, nothing is sent.
    if (identifierBox && !identifierBox.check()) return;
    chosenIdentifier = identifierBox ? identifierBox.value() : '';
    closeCellDetail(); go();
  });
  // The shape is flipped here, before the write (see wireShapeFlip).
  if (bp.shape_plan) wireShapeFlip(body, bp.shape_plan, (shape) => { chosenShape = shape; });
}

/** Why a typed identifier cannot be used, or '' when it can. Mirrors blueprint_kinds.validate_identifier,
 *  which the server runs again. */
export function identifierProblem(raw) {
  const text = (raw || '').trim();
  if (!text) return 'needed';
  if (text.includes('::')) return 'no "::"';
  if (!/^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$/.test(text)) return 'not valid';
  return '';
}

/** The identifier input, shown ONLY when a blueprint of this kind already exists for the repository (the
 *  identity is kind + repository; a second one is named by a person, never by its root cluster). State is a
 *  short word beside the field; the sentence is on demand (the field's title). */
export function identifierBoxHtml(identity) {
  if (!identity || !identity.needs_identifier) return '';
  return `<div data-identifier-box class="mt-s2 border-t border-rule pt-s2" title="${esc(identity.sentence || '')}">
    <label class="text-caveat text-ink"><span class="text-chrome-muted">identifier</span> ·
      <input data-identifier-input type="text" maxlength="64" autocomplete="off" aria-invalid="false"
        class="rounded-sm border border-rule bg-transparent px-2 py-[2px] font-mono text-caveat text-ink"></label>
    <span data-identifier-word class="text-provenance text-ink-muted">needed</span>
  </div>`;
}

/** Wires the identifier input. Returns null when none is shown; otherwise {value(), check()}: `check()`
 *  says the problem as a word beside the field and returns whether the identifier can be sent. */
export function wireIdentifier(root, identity) {
  const input = root.querySelector('[data-identifier-input]');
  if (!identity?.needs_identifier || !input) return null;
  const word = root.querySelector('[data-identifier-word]');
  const show = (problem, loud) => {
    input.setAttribute('aria-invalid', problem && loud ? 'true' : 'false');
    input.classList.toggle('border-accent', Boolean(problem && loud));
    if (word) {
      word.textContent = problem || 'ready';
      word.classList.toggle('text-accent-ink', Boolean(problem && loud));
    }
  };
  input.addEventListener('input', () => show(identifierProblem(input.value), false));
  return {
    value: () => input.value.trim(),
    check: () => {
      const problem = identifierProblem(input.value);
      show(problem, true);
      if (problem && input.focus) input.focus();
      return !problem;
    },
  };
}

/** Wires the Container/Contents options in the dialog. A click is an EXPLICIT choice and is reported even
 *  when it equals the shape the preview displayed: the preview cannot know the content pack, so the server's
 *  default can differ, and a person who deliberately picked a shape must not be overridden. A refused flip
 *  (no root to be the container) keeps the default and says why instead of reading "chosen". */
export function wireShapeFlip(root, plan, onChoose) {
  root.querySelectorAll('[data-shape-option]').forEach((b) => b.addEventListener('click', () => {
    const asked = b.dataset.shapeOption;
    const alt = plan.alternatives?.[asked];
    const now = alt?.shape || asked;
    onChoose(now);
    root.querySelectorAll('[data-shape-option]').forEach((o) => {
      const on = o.dataset.shapeOption === now;
      o.setAttribute('aria-pressed', on ? 'true' : 'false');
      o.querySelector('[data-shape-word]').textContent = on ? 'chosen' : '';
    });
    const line = root.querySelector('[data-shape-line]');
    const why = root.querySelector('[data-shape-why]');
    if (alt && line) line.textContent = alt.words;
    if (alt && why) why.textContent = alt.flip_refused ? `${alt.why} · ${alt.flip_refused}` : alt.why;
  }));
}

/** The manifest line for a blueprint's shape (DESIGN-BLUEPRINT-BENCHMARK-EGERIA-WORKSPACES.md 6a): which
 *  shape RE will write and why, with the other one offered as a flip. Egeria draws the diagram from the
 *  metadata; this names only what is written. */
export function shapeManifestHtml(plan) {
  if (!plan) return '';
  const opt = (shape, label) => `<button type="button" data-shape-option="${esc(shape)}" aria-pressed="${plan.shape === shape ? 'true' : 'false'}"
      class="cursor-pointer rounded-sm border border-rule bg-transparent px-2 py-[2px] text-caveat text-ink">${esc(label)} <span data-shape-word class="text-ink-muted">${plan.shape === shape ? 'chosen' : ''}</span></button>`;
  return `<div data-shape-manifest class="mt-s2 border-t border-rule pt-s2">
    <div class="text-caveat text-ink"><span class="text-chrome-muted">shape</span> · <span data-shape-line>${esc(plan.words)}</span></div>
    <div data-shape-why class="text-provenance text-ink-muted">${esc(plan.why)}${plan.flip_refused ? ` · ${esc(plan.flip_refused)}` : ''}</div>
    <div class="mt-s1 flex gap-s2">${opt('container', 'Container')}${opt('contents', 'Contents')}</div>
  </div>`;
}

/** The diagram beside the tree. It is already verdict-aware -- rendered
 *  fresh on every read, rejected dropped, accepted solid, undecided dashed
 *  -- so accepting a branch and re-reading redraws it; nothing to build for
 *  that. What it is not is the acting surface: it comes back from Kroki as
 *  a finished SVG. It says its two ceilings in its own caption. The tree
 *  is the surface that scales; the diagram is the one that explains. */
async function renderComponentDiagram(slug, host) {
  if (!host) return;
  let fact;
  try {
    const res = await (takePrefetched(slug, 'diagram') || getBulkFacts([slug], ['architecture_diagram'], apiEntityType(state.resourceType)));
    fact = (((res.subjects || {})[slug]) || []).find((f) => f.analysis_id === 'architecture_diagram');
  } catch { fact = null; }
  if (slug !== state.selectedSlug) return;
  const src = fact?.value?.mermaid;
  if (!src) { host.innerHTML = `<div class="text-provenance text-ink-muted">No diagram to read — architecture_diagram has not rendered one for this resource.</div>`; return; }
  // Which extractor drew this, and what else is on file — a value the
  // classic Curate panel already showed and this surface silently dropped.
  // RULING-WHAT-A-VERDICT-IS-ABOUT.md §3: `fact.value.perspective` here is a
  // run_label ("detect"/"coupling"), not a Component.perspective reading, so
  // it renders as "found by", never "perspective".
  const foundBy = fact.value.perspective
    ? `<div class="text-provenance text-ink-muted mt-s1">found by ${esc(fact.value.perspective)}` +
      ((fact.value.other_perspectives_available || []).length
        ? ` · ${esc(fact.value.other_perspectives_available.join(', '))} also on file`
        : '') + `</div>`
    : '';
  const prevSvg = host.querySelector('[data-diagram-svg] svg') ? host.querySelector('[data-diagram-svg]').innerHTML : '';
  host.innerHTML = `<div class="mb-s1 text-caps uppercase tracking-caps text-ink">The diagram reads; the tree acts</div>
    <div class="text-provenance text-ink-muted">${tnum(esc(fact.value.caption || fact.headline || ''))}</div>
    ${foundBy}
    <div data-diagram-svg class="mt-s1 w-full overflow-auto rounded-sm border border-rule-strong" style="max-height:min(60vh,560px)">${prevSvg || 'rendering…'}</div>`;
  try {
    const t = tokens();
    const prepped = mermaidForKroki(src);
    const res = await fetch('/api/diagrams/mermaid', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source: prepped.source }) });
    if (!res.ok) throw new Error(`${res.status} from the renderer`);
    const raw = await res.text();
    if (!raw.includes('<svg')) throw new Error('the renderer returned no SVG');
    const slot = host.querySelector('[data-diagram-svg]');
    slot.innerHTML = raw;
    const svgEl = slot.querySelector('svg');
    if (svgEl) { themeSvgElement(svgEl, t); svgEl.removeAttribute('height'); svgEl.style.maxWidth = '100%'; svgEl.style.height = 'auto'; }
  } catch (err) {
    const slot = host.querySelector('[data-diagram-svg]');
    if (slot) slot.innerHTML = `<div class="p-s2 text-provenance text-accent-ink">The diagram could not be rendered: ${esc(err.message)}. The source is on the Analysis pane.</div>`;
  }
}

/** The shared preview dialog, because rule 4 makes it mandatory: the act
 *  names what it would do before it does it. Rejecting creates nothing in
 *  Egeria, so it records at once.
 *
 *  `scopes` may be several branches at once (the tree's own multi-select,
 *  SPEC-CURATE-SELECTION-AND-BLUEPRINTS.md §1) -- the confirmation states
 *  the TOTAL scope count across every selected branch before acting ("two
 *  branches selected — 20 scopes total"), not just that an action ran.
 *  `onDone` (optional) fires once the verdicts are recorded -- the
 *  selection-clearing callback, so a selection is not left checked against
 *  branches that were just acted on. */
/** The pressed control shows each phase on itself: pending the instant it is pressed, then settled
 *  or failed. `el` is the button (or null); the caller's markup is restored on failure so a retry works. */
function pressPhase(el, phase, word, title = '') {
  if (!el || !el.isConnected) return;
  if (!el.dataset.idleHtml) el.dataset.idleHtml = el.innerHTML;
  el.dataset.phase = phase;
  el.disabled = phase === 'pending';
  el.setAttribute('aria-busy', phase === 'pending' ? 'true' : 'false');
  el.innerHTML = stateCue(phase === 'pending' ? 'running' : phase === 'done' ? 'measured' : 'error', word, title);
}
function recordVerdicts(slug, scopes, verdict, { count, low, exists = 0, confirmAlways = false, countRecorded = null, onStart = null, onSettled = null, onlyScopes = [] }, onDone, pressedEl = null) {
  const status = $('component-tree-status');
  const accepting = verdict === 'accepted';
  const go = async () => {
    if (pressedEl && pressedEl.dataset.phase === 'pending') return;       // a second press is ignored
    if (onStart) onStart();
    try {
    pressPhase(pressedEl, 'pending', accepting ? 'accepting…' : 'rejecting…');
    if (status) status.innerHTML = stateCue('running', 'recording…');
    try {
      const out = await postBranchVerdicts(slug, scopes, verdict, '', onlyScopes);
      pressPhase(pressedEl, 'done', accepting ? 'accepted' : 'rejected');
      // A decision only: the words say so. Nothing reaches Egeria until Publish.
      const settled = accepting
        ? `<span class="text-state-ok">→ <span class="tnum">${out.verdicts.length}</span> verdict${out.verdicts.length === 1 ? '' : 's'} recorded · accepted · not in Egeria until Publish</span>`
        : `<span class="text-state-ok">→ rejected · nothing written to Egeria</span>`;
      if (status) status.innerHTML = settled;
      onDone?.();
      document.dispatchEvent(new CustomEvent(ARCHITECTURE_CHANGED, { detail: { slug } }));   // Publish re-reads its list
      // The tree redraws from a re-read and replaces its status line, so the settled words are written
      // again on the new line: the result stays on screen after the redraw.
      await renderComponentTree(slug);
      const fresh = $('component-tree-status');
      if (fresh) fresh.innerHTML = settled;
    } catch (err) {
      const why = err.status === 401 ? 'sign in to record a verdict' : err.status === 403 ? `not permitted · ${err.message}` : err.message;
      pressPhase(pressedEl, 'error', 'failed · press to retry', why);
      if (pressedEl) pressedEl.disabled = false;
      let words = `not recorded — ${esc(why)}`;
      if (countRecorded && scopes.length > 1) {
        // A batch is posted row by row, so a failure can leave part of it recorded: count what landed.
        try {
          const n = await countRecorded();
          if (n > 0) words = `partly recorded · <span class="tnum">${n}</span> of <span class="tnum">${scopes.length}</span> recorded, <span class="tnum">${scopes.length - n}</span> failed — ${esc(why)}`;
        } catch { /* the plain sentence stands */ }
        await renderComponentTree(slug);              // the rows show what really landed; the words are written after it
      }
      const st = $('component-tree-status') || status;
      if (st) st.innerHTML = `<span class="text-accent-ink">${words}</span>`;
    }
    } finally { if (onSettled) onSettled(); }
  };
  if (confirmAlways && count < 1) return;
  if (!confirmAlways) {
    if (verdict !== 'accepted' || count <= 1) { go(); return; }   // rejecting creates nothing; one leaf needs no preview
  }
  const scopeLabel = scopes.length > 1
    ? `${scopes.length} branches selected — ${count} scope${count === 1 ? '' : 's'} total`
    : `${scopes.join(', ')} · ${count} component${count === 1 ? '' : 's'}`;
  if (!accepting) {
    const rel = openDialog('Reject at the branch', scopeLabel);
    const rbody = rel.querySelector('#wl-detail-body');
    rbody.innerHTML = `
    <p class="text-caveat text-ink"><span class="tnum">${count}</span> undecided component${count === 1 ? '' : 's'} will be recorded as rejected. Nothing is written to Egeria, and nothing already there is removed.</p>
    <p class="text-caveat text-ink-muted">A verdict is a new row; changing it later is another row, and the trail keeps both.</p>
    <div class="mt-s3 flex gap-s3">
      <button data-act="confirm" class="cursor-pointer rounded-sm border border-accent bg-transparent px-3 py-[3px] text-answer text-accent-ink">Reject ${count}</button>
      <button data-act="close" class="cursor-pointer bg-transparent p-0 text-provenance text-ink-muted underline">not now</button>
    </div>`;
    rbody.querySelector('[data-act="confirm"]').addEventListener('click', () => { closeCellDetail(); go(); });
    return;
  }
  const el = openDialog('Accept at the branch', scopeLabel);
  const body = el.querySelector('#wl-detail-body');
  body.innerHTML = `
    <p class="text-caveat text-ink"><span class="tnum">${count}</span> component${count === 1 ? '' : 's'}${low ? `, <span class="tnum">${low}</span> of them at or below 50% confidence` : ''}.
      <span class="tnum">${Math.max(0, count - exists)}</span> will be recorded as accepted${exists ? `; <span class="tnum">${exists}</span> already accepted` : ''}.</p>
    <p class="text-caveat text-ink-muted">Accepting is a decision: nothing is written to Egeria until you press Publish, and Publish lists what it will write first.</p>
    <p class="text-caveat text-ink-muted">A verdict is a new row; changing it later is another row, and the trail keeps both.</p>
    <div class="mt-s3 flex gap-s3">
      <button data-act="confirm" class="cursor-pointer rounded-sm border border-accent bg-transparent px-3 py-[3px] text-answer text-accent-ink">Accept ${count}</button>
      <button data-act="close" class="cursor-pointer bg-transparent p-0 text-provenance text-ink-muted underline">not now</button>
    </div>`;
  body.querySelector('[data-act="confirm"]').addEventListener('click', () => { closeCellDetail(); go(); });
}
