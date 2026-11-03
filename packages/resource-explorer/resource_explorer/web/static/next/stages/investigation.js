/* Investigation — the frame, not a stage.
 *
 * docs/investigation-framing-design.md §1: Investigation sits ABOVE the
 * eight canonical intents. It answers "why does this body of work exist",
 * not "what kind of work is happening" — that is why `STAGES` in app.js
 * marks it `class: 'frame'` rather than `run`/`cross-cutting`, and why this
 * pane bypasses the generic Questions-checklist engine entirely, the same
 * shape as Understanding (charts) and Automate (subscriptions/schedules):
 * `loadPane()` calls `renderInvestigation()` directly for
 * `state.stage === 'investigation'` and returns.
 *
 * This used to be a 17-line honest placeholder pointing at classic's
 * `index.html` as the only live implementation. It is now a real, in-place
 * port of that implementation against the same `/api/investigations/*`
 * surface (`web/routes/investigations.py`) — list/create, per-investigation
 * detail (members across resource types, per-member disposition,
 * next-steps, purposes, classification), and the Egeria bind/promote/sync/
 * reclassify flow. It does NOT duplicate the sidebar's own "current
 * investigation" selector (app.js's `investigationBarHtml()`/
 * `setInvestigation()`) — that stays the chrome-level scoping control; this
 * pane is where an investigation itself is managed (created, described,
 * classified, bound to Egeria, closed), the two working together the same
 * way classic's nav-level Investigations tab and its per-repo "add to
 * investigation" toggle always have.
 */
import { mountDataRequestBand } from '/static/next/data-request.js';
import { ago, readBackAt } from '/static/next/format.js';

/** A sentence on the element it is about (the owner's rule: state on the element, not a pop-up): a small
 *  warning span right after `el`, replaced by the next one. */
function inlineNote(el, text) {
  let n = el.nextElementSibling;
  if (!n || !n.hasAttribute('data-inline-note')) {
    n = document.createElement('span');
    n.setAttribute('data-inline-note', '1');
    n.setAttribute('role', 'status');
    n.className = 'ml-s1 text-provenance text-state-warn';
    el.insertAdjacentElement('afterend', n);
  }
  n.textContent = text;
}
import {
  listInvestigations, getInvestigationClassifications,
  getInvestigation, createInvestigation, updateInvestigation,
  listInvestigationMembers, addInvestigationMember, removeInvestigationMember,
  closeInvestigation, suspendInvestigation, reopenInvestigation,
  bindInvestigationEgeriaProject, promoteInvestigation, reclassifyInvestigation,
  relinkInvestigationMembers, syncInvestigationEgeria, getInvestigationLinkStatus,
  getInvestigationDispositions, setInvestigationDisposition, getInvestigationNextSteps,
  fetchScopeCsv, getJournal, getCurateFeedbackState, publishInvestigationJournal,
} from '/static/re-api.js';
import {
  state, esc, $, icon, setInvestigation, refreshInvestigationsAndSidebar,
  ensureResourceListLoaded,
} from '/static/next/app.js';
import {
  loadPurposes, purposesNow, purposePickerHtml, readPurposePicker, wirePurposePicker, purposesSummaryHtml,
} from '/static/next/purposes.js';
import { openDialog, closeCellDetail } from '/static/next/worklist.js';
import { renderMarkdown } from '/static/next/markdown.js';
import { saveCsv } from '/static/next/download.js';
import { inPageConfirm } from '/static/next/confirm-dialog.js';
import { cue } from '/static/next/stages/repo-manifest.js';
import { mayOfferDelete, deleteButtonHtml, showRememberedStop, startDelete } from '/static/next/stages/investigation-delete.js';

/** Brief Z3: a refusal by the zone check (403) is the no_access cue plus "not permitted", the reason on hover;
 *  any other failure keeps its own words. */
const refusalHtml = (err) => (err && err.status === 403
  ? cue('no_access', 'not permitted', err.message)
  : `<span class="text-state-warn">${esc(err?.message || '')}</span>`);

/** A member line of a promote/relink result: a member the check refused carries the cue, its reason on hover. */
const memberLine = (m) => {
  const name = esc(m.entity_slug || m.entity_type || '?');
  const reason = String(m.reason || '');
  if (reason.startsWith('not permitted')) {
    return `<span data-member-not-permitted="${name}">${cue('no_access', 'not permitted', reason.replace(/^not permitted · /, ''))} ${name}</span>`;
  }
  return esc(`${m.entity_slug || m.entity_type || '?'}${reason ? ` (${reason})` : ''}`);
};

// Module-local view state — which investigation (if any) is open, and
// whether the list includes closed ones. Not on `state`: nothing outside
// this pane reads it, same reasoning as automate.js's `_tab`.
let _detailSlug = null;
// Bumped by every renderDetail(); a response that arrives after a newer
// render, a different investigation, or leaving the stage is dropped.
let _detailSeq = 0;
let _includeClosed = false;

// Vocabularies are server-served and cached here for the life of the page. Classifications are
// registry.py's PROJECT_CLASSIFICATIONS. Purposes are Egeria's list (owner ruling 2026-10-13), loaded once
// through purposes.js, which every purpose picker shares; the answer carries where it came from (Egeria,
// a labelled cache, or RE's shipped seed).
let _classifications = null;

const DISPOSITION_GLYPH = {
  tracking: '👁', investigating: '🔬', recommended: '👍',
  using: '✅', abandoned: '🪦', ignored: '🚫',
};

export async function investigationVocab() {
  const purposeVocab = await loadPurposes();
  if (!_classifications) {
    try { _classifications = await getInvestigationClassifications(); } catch {
      _classifications = { classifications: [], bindings: [], default_classification: 'StudyProject', default_binding: 'egeria' };
    }
  }
  return { purposes: purposeVocab.purposes, purposeVocab, classifications: _classifications };
}

export async function renderInvestigation() {
  if (_detailSlug) return renderDetail(_detailSlug);
  return renderList();
}

/** THE refresh mechanism for the open investigation page. Every writer that
 *  changes an investigation's members from outside this pane (the Find
 *  databases dialog's confirm, the sidebar's "＋ scope" / "− scope") calls
 *  this once after its writes. It re-fetches and re-renders only when that
 *  investigation's detail is the pane the person is looking at; otherwise it
 *  does nothing, and the next open fetches fresh (this file caches nothing). */
export function refreshOpenInvestigation(slug) {
  if (state.stage === 'investigation' && _detailSlug && _detailSlug === slug) {
    return renderDetail(slug);
  }
  return Promise.resolve();
}

/** Called from app.js's resource header ("Open Investigation →", the /next
 *  equivalent of classic's index.html link at ~line 5779) so navigating
 *  into the Investigation frame lands on the resource's current
 *  investigation, not the bare list. */
export function openInvestigationDetail(slug) {
  _detailSlug = slug;
}

/* ── List ─────────────────────────────────────────────────────────────── */

function statusGlyph(status) {
  if (status === 'closed') return '<span class="text-ink-muted" title="closed">● closed</span>';
  if (status === 'suspended') return '<span class="text-state-warn" title="suspended">◐ suspended</span>';
  return '<span class="text-state-ok" title="open">○ open</span>';
}

function bindingGlyph(inv) {
  // `egeria_context` (registry.py get_investigation()) is a small object,
  // not a display string — {status, egeria_project_guid,
  // egeria_project_qualified_name, free_text_name}. Use the qualified name
  // straight off the row, same as the detail view does.
  if (!inv.egeria_project_guid && inv.egeria_context && inv.egeria_context.status === 'unbound') {
    // After an Egeria reset: the project it named is gone. Not "local" (a choice) and not "Egeria" (a project).
    return '<span class="text-state-warn" title="unbound by reset \u00b7 rebind to recreate">unbound by reset</span>';
  }
  return inv.egeria_project_guid
    ? `<span title="${esc(inv.egeria_project_qualified_name || 'bound to an Egeria Project')}">☁ Egeria</span>`
    : '<span class="text-ink-muted" title="no Egeria Project">🏠 local</span>';
}

async function renderList() {
  const el = $('content');
  el.innerHTML = listHeaderHtml() + '<p class="text-answer text-ink-muted">Loading…</p>';
  bindListHeader();

  let rows;
  // The list is drawn without waiting for the purposes list (a slow Egeria read must not hold the page);
  // the cells are filled with their cues once it arrives. Until then no cue is drawn: no list, no claim.
  const purposeNames = purposesNow()?.purposes || [];
  try {
    rows = await listInvestigations({ includeClosed: _includeClosed });
  } catch (err) {
    el.innerHTML = listHeaderHtml()
      + `<p class="max-w-[70ch] text-answer text-state-warn">Could not load investigations: ${esc(err.message)}</p>`;
    bindListHeader();
    return;
  }

  const body = rows.length ? `<table class="w-full text-left">
      <thead><tr class="border-b border-rule text-caps uppercase tracking-caps text-ink-muted">
        <th class="pb-s2 pr-s3 font-normal">Investigation</th>
        <th class="pb-s2 pr-s3 font-normal">Purposes</th>
        <th class="pb-s2 pr-s3 font-normal">Status</th>
        <th class="pb-s2 pr-s3 font-normal">Scope</th>
        <th class="pb-s2 font-normal">Egeria</th>
      </tr></thead>
      <tbody>${rows.map((inv) => `
        <tr class="cursor-pointer border-b border-rule hover:bg-paper-surface" data-open-inv="${esc(inv.slug)}">
          <td class="py-s2 pr-s3 text-answer text-ink">
            ${inv.slug === state.investigation ? '<span title="current investigation">★ </span>' : ''}
            ${esc(inv.display_name || inv.slug)}
            ${inv.visibility === 'private' ? `<span class="ml-[4px] text-caveat text-ink-muted" title="${esc(inv.visibility_note || 'visible only to its creator')}">🔒</span>` : ''}
          </td>
          <td class="py-s2 pr-s3 text-caveat text-ink-muted" data-inv-purposes-cell="${esc(inv.slug)}">${purposesSummaryHtml(inv, purposeNames) || '—'}</td>
          <td class="py-s2 pr-s3 text-caveat">${statusGlyph(inv.status)}</td>
          <td class="py-s2 pr-s3 text-caveat tnum text-ink-muted">${inv.member_count ?? 0}</td>
          <td class="py-s2 text-caveat">${bindingGlyph(inv)}</td>
        </tr>`).join('')}
      </tbody>
    </table>` : '<p class="text-answer text-ink-muted">No investigations yet — create one to start scoping a body of work.</p>';

  el.innerHTML = listHeaderHtml() + body;
  bindListHeader();
  el.querySelectorAll('[data-open-inv]').forEach((tr) => tr.addEventListener('click', () => {
    _detailSlug = tr.dataset.openInv;
    renderInvestigation();
  }));
  if (!purposeNames.length) {
    loadPurposes().then((vocab) => {
      const bySlug = new Map(rows.map((inv) => [inv.slug, inv]));
      el.querySelectorAll('[data-inv-purposes-cell]').forEach((cell) => {
        const inv = bySlug.get(cell.dataset.invPurposesCell);
        if (inv) cell.innerHTML = purposesSummaryHtml(inv, vocab.purposes) || '—';
      });
    });
  }
}

function listHeaderHtml() {
  return `<div class="mb-s3 flex items-center gap-s3">
      <h3 class="m-0 font-heading text-name font-normal text-ink">Investigations</h3>
      <label class="ml-s2 inline-flex cursor-pointer items-center gap-[5px] text-caveat text-ink-muted">
        <input type="checkbox" id="inv-include-closed" ${_includeClosed ? 'checked' : ''}>
        Show closed
      </label>
      <button data-act="inv-new" type="button"
        class="ml-auto cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px]
               text-caveat text-accent-ink">+ New investigation</button>
    </div>
    <p class="max-w-[70ch] text-caveat text-ink-muted">
      An investigation is the frame a body of work runs inside — why it exists, not what kind of
      work it is. Resources can belong to several at once; closing one keeps its findings queryable,
      it just drops out of the default scope.
    </p>
    <div class="my-s3 h-px bg-rule"></div>`;
}

function bindListHeader() {
  const el = $('content');
  el.querySelector('#inv-include-closed')?.addEventListener('change', (e) => {
    _includeClosed = e.target.checked;
    renderList();
  });
  el.querySelector('[data-act="inv-new"]')?.addEventListener('click', () => openCreateDialog());
}

/* ── Create ───────────────────────────────────────────────────────────── */

/** The one "New investigation" dialog. From the Investigations list it
 *  navigates to the new investigation's page. The picker (investigation-
 *  picker.js) passes `onCreated(inv)` instead, so "start a new one" reuses this
 *  form and carries on with whatever the person was doing, not a page change. */
export async function openCreateDialog({ onCreated = null } = {}) {
  const { purposeVocab, classifications } = await investigationVocab();
  const d = openDialog('New investigation', '');
  const body = d.querySelector('#wl-detail-body');
  body.innerHTML = `
    <label class="mb-[3px] block text-caveat text-ink-muted">Name</label>
    <input id="inv-new-name" type="text" class="mb-s2 w-full rounded-sm border border-rule bg-paper px-2 py-1 text-answer text-ink">
    <label class="mb-[3px] block text-caveat text-ink-muted">Description</label>
    <textarea id="inv-new-desc" rows="2" class="mb-s2 w-full rounded-sm border border-rule bg-paper px-2 py-1 text-caveat text-ink"></textarea>
    <label class="mb-[3px] block text-caveat text-ink-muted">Purposes</label>
    <div class="mb-s2">${purposePickerHtml({ prefix: 'inv-new', vocab: purposeVocab })}</div>
    <label class="mb-[3px] block text-caveat text-ink-muted">Kind</label>
    <select id="inv-new-class" class="mb-s2 w-full rounded-sm border border-rule bg-paper px-2 py-1 text-caveat text-ink">
      ${classifications.classifications.map((c) => `<option value="${esc(c.name)}" ${
        c.name === classifications.default_classification ? 'selected' : ''}>${esc(c.label || c.name)}</option>`).join('')}
    </select>
    <div id="inv-new-hyp-wrap" class="mb-s2 hidden">
      <label class="mb-[3px] block text-caveat text-ink-muted">Hypothesis (required for Experiment)</label>
      <input id="inv-new-hyp" type="text" class="w-full rounded-sm border border-rule bg-paper px-2 py-1 text-caveat text-ink">
    </div>
    <label class="mb-[3px] block text-caveat text-ink-muted">Where it lives</label>
    <select id="inv-new-binding" class="mb-s3 w-full rounded-sm border border-rule bg-paper px-2 py-1 text-caveat text-ink">
      ${classifications.bindings.map((b) => `<option value="${esc(b.name)}" ${
        b.name === classifications.default_binding ? 'selected' : ''}>${esc(b.label || b.name)}</option>`).join('')}
    </select>
    <div id="inv-new-error" class="mb-s2 text-caveat text-state-warn"></div>
    <div class="flex gap-s2">
      <button id="inv-new-submit" type="button"
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-caveat text-accent-ink">Create</button>
      <button data-act="close" type="button" class="cursor-pointer bg-transparent text-caveat text-ink-muted underline">Cancel</button>
    </div>`;

  const classSel = body.querySelector('#inv-new-class');
  const hypWrap = body.querySelector('#inv-new-hyp-wrap');
  const requiresHyp = (name) => classifications.classifications.find((c) => c.name === name)?.requires_hypothesis;
  const syncHyp = () => {
    const need = requiresHyp(classSel.value);
    hypWrap.classList.toggle('hidden', !need);
    // Cleared, not hidden-but-still-posted — HYPOTHESIS_REQUIRED_FOR is about
    // Experiment specifically, and a stale value from switching kinds back
    // and forth must not silently ride along on a non-Experiment create.
    if (!need) body.querySelector('#inv-new-hyp').value = '';
  };
  classSel.addEventListener('change', syncHyp);
  syncHyp();
  wirePurposePicker(body, 'inv-new');

  body.querySelector('#inv-new-submit').addEventListener('click', async () => {
    const name = body.querySelector('#inv-new-name').value.trim();
    const errEl = body.querySelector('#inv-new-error');
    if (!name) { errEl.textContent = 'Name is required.'; return; }
    const picked = readPurposePicker(body, 'inv-new');
    const projectClassification = classSel.value;
    const hypothesis = body.querySelector('#inv-new-hyp').value.trim();
    if (requiresHyp(projectClassification) && !hypothesis) {
      errEl.textContent = 'This kind requires a hypothesis.';
      return;
    }
    const btn = body.querySelector('#inv-new-submit');
    btn.disabled = true;
    btn.textContent = 'Publishing…';
    try {
      const inv = await createInvestigation({
        displayName: name,
        description: body.querySelector('#inv-new-desc').value.trim(),
        purposes: picked.purposes,
        purposeText: picked.purpose_text,
        projectClassification,
        egeriaBinding: body.querySelector('#inv-new-binding').value,
        hypothesis,
      });
      closeCellDetail();
      await refreshInvestigationsAndSidebar();
      if (onCreated) { await onCreated(inv); return; }
      _detailSlug = inv.slug;
      await renderInvestigation();
    } catch (err) {
      btn.disabled = false;
      btn.textContent = 'Create';
      errEl.textContent = err.message;
    }
  });
}

/* ── Detail ───────────────────────────────────────────────────────────── */

function detailHeaderHtml(inv) {
  return `<div class="mb-s3 flex items-center gap-s3">
      <button data-act="inv-back" type="button"
        class="cursor-pointer bg-transparent text-caveat text-accent-ink underline">← Investigations</button>
      <h3 class="m-0 font-heading text-name font-normal text-ink">${esc(inv.display_name || inv.slug)}</h3>
      ${statusGlyph(inv.status)}
    </div>`;
}

async function renderDetail(slug) {
  const el = $('content');
  const seq = ++_detailSeq;
  const stale = () => seq !== _detailSeq || _detailSlug !== slug || state.stage !== 'investigation';
  el.innerHTML = `<p class="text-answer text-ink-muted">Loading…</p>`;

  let inv, members, dispositions, nextSteps;
  try {
    [inv, members, dispositions, nextSteps] = await Promise.all([
      getInvestigation(slug),
      listInvestigationMembers(slug),
      getInvestigationDispositions(slug),
      getInvestigationNextSteps(slug),
    ]);
  } catch (err) {
    if (stale()) return;
    el.innerHTML = `<button data-act="inv-back" type="button"
        class="mb-s3 cursor-pointer bg-transparent text-caveat text-accent-ink underline">← Investigations</button>
      <p class="max-w-[70ch] text-answer text-state-warn">Could not load '${esc(slug)}': ${esc(err.message)}</p>`;
    el.querySelector('[data-act="inv-back"]').addEventListener('click', () => { _detailSlug = null; renderInvestigation(); });
    return;
  }

  if (stale()) return;
  // Link status is a read of what relink recorded; a failed read is "unavailable", never a zero.
  _linkStatus[slug] = null;
  _linkMembers[slug] = null;
  if (inv.egeria_project_guid) {
    try {
      const ls = await getInvestigationLinkStatus(slug);
      if (ls && ls.counts) _linkStatus[slug] = ls.counts;
      // The per-member rows come from the SAME response: state (linked | not_linked | could_not_tell) and reason.
      if (ls && Array.isArray(ls.members)) _linkMembers[slug] = ls.members;
    } catch { /* leaves null: shown as unavailable */ }
    if (stale()) return;
  }
  const { classifications, purposes: purposeNames } = await investigationVocab();
  if (stale()) return;
  const classLabel = classifications.classifications.find((c) => c.name === inv.project_classification)?.label
    || inv.project_classification;

  const isCurrent = inv.slug === state.investigation;
  const lifecycleButtons = inv.status === 'open'
    ? `<button data-act="inv-suspend" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60">Suspend</button>
       <button data-act="inv-close" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60">Close</button>`
    : `<button data-act="inv-reopen" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60">Reopen</button>`;

  el.innerHTML = `${detailHeaderHtml(inv)}
    <div class="mb-s3 flex flex-wrap items-center gap-s2 text-caveat text-ink-muted">
      <span title="Project classification">${esc(classLabel)}</span>
      ${inv.project_classification === 'Experiment' && inv.hypothesis
        ? `<span title="${esc(inv.hypothesis)}">· hypothesis set</span>` : ''}
      <span>·</span>
      ${bindingGlyph(inv)}
      ${purposesSummaryHtml(inv, purposeNames) ? `<span data-inv-purposes>· ${purposesSummaryHtml(inv, purposeNames)}</span>` : ''}
      ${inv.visibility === 'private' ? `<span title="${esc(inv.visibility_note || '')}">· 🔒 private</span>` : ''}
    </div>
    <p class="max-w-[70ch] text-answer text-ink">${esc(inv.description || 'No description.')}</p>
    <div class="mb-s3 flex flex-wrap gap-s2">
      <button data-act="inv-make-current" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60" ${isCurrent ? 'disabled' : ''}>${
        isCurrent ? 'Current investigation' : 'Make current'}</button>
      <button data-act="inv-edit" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60">Edit</button>
      <button data-act="inv-reclassify" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60">Reclassify…</button>
      ${lifecycleButtons}
      ${mayOfferDelete(inv, state.me) ? deleteButtonHtml() : ''}
    </div>
    <div id="inv-delete-result"></div>
    <div id="inv-edit-form"></div>
    <div id="inv-reclass-form"></div>

    <div class="my-s3 h-px bg-rule"></div>
    <h4 class="m-0 mb-s2 font-heading text-subtab font-normal text-ink">Next steps</h4>
    ${nextSteps.steps?.length ? `<ul class="mb-s3 list-none p-0">${nextSteps.steps.map((s) => `
      <li class="mb-s2 rounded-sm border border-rule p-s2">
        <div class="text-answer text-ink">${esc(s.title)}</div>
        <div class="text-caveat text-ink-muted">${esc(s.detail || '')}</div>
        ${s.action ? `<button data-next-step="${esc(s.action)}" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60 mt-s1">Go</button>` : ''}
      </li>`).join('')}</ul>`
      : `<p class="mb-s3 text-caveat text-ink-muted">${nextSteps.complete ? 'Nothing outstanding.' : 'No next steps offered.'}</p>`}

    <div class="my-s3 h-px bg-rule"></div>
    <div id="inv-data-request" data-dr-band></div>

    <div class="my-s3 h-px bg-rule"></div>
    <div class="mb-s2 flex items-center gap-s2">
      <h4 class="m-0 font-heading text-subtab font-normal text-ink">Egeria</h4>
    </div>
    ${egeriaSectionHtml(inv)}
    <div id="inv-egeria-form"></div>

    <div class="my-s3 h-px bg-rule"></div>
    <div id="inv-scope" class="mb-s2 flex items-center gap-s3">
      <h4 class="m-0 font-heading text-subtab font-normal text-ink">Scope · <span class="tnum">${members.length}</span></h4>
      <button data-act="inv-add-menu" type="button" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60">＋ add…</button>
      <button data-act="inv-export-scope" type="button"
        class="cursor-pointer bg-transparent text-caveat text-accent-ink underline"
        title="Download the in-scope members as CSV. Loading it into another investigation adds the resources; the status_ columns are for a reader and are never read back."
        >Export CSV</button>
      <span id="inv-scope-note" class="text-caveat text-ink-muted"></span>
    </div>
    <div id="inv-add-menu"></div>
    ${addMemberFormHtml()}
    ${membersTableHtml(inv, members, dispositions)}

    <div class="my-s3 h-px bg-rule"></div>
    <div id="inv-journal"><p class="text-caveat text-ink-muted">Loading the journal…</p></div>`;

  bindDetail(inv, members);
  mountDataRequestBand(document.getElementById('inv-data-request'), inv.slug);
  loadJournal(inv, stale);
}

/** The investigation's journal: dated entries, newest first, each saved from a chat answer or written by hand.
 *  A failed read says so; it is never shown as an empty journal. */
async function loadJournal(inv, stale) {
  const host = $('inv-journal');
  if (!host) return;
  let entries;
  try {
    const res = await getJournal(inv.slug, 'investigation');
    // A reply with no `entries` list is a failed read, not an empty journal.
    if (!res || !Array.isArray(res.entries)) throw new Error('the server sent no entries list');
    entries = res.entries;
  } catch (err) {
    if (!stale()) host.innerHTML = `<p class="text-caveat text-state-warn">The journal could not be read: ${esc(err.message)}</p>`;
    return;
  }
  if (stale()) return;
  // Each entry's state in Egeria. A failed read leaves the words off; it never claims "published".
  let words = {};
  let toSend = 0;
  try {
    const plan = await getCurateFeedbackState('investigation', inv.slug);
    words = Object.fromEntries((plan.notes || []).map((n) => [n.id, n.word]));
    toSend = plan.to_send || 0;
  } catch { /* leaves the words off */ }
  if (stale()) return;
  const bodies = await Promise.all(entries.map((e) => renderMarkdown(e.body)));
  if (stale()) return;
  const promoted = Boolean(inv.egeria_project_guid);
  const where = promoted
    ? 'Your entries go to the Egeria Project as Notes when you publish them.'
    : 'Kept in RE. They go to Egeria once this investigation is promoted.';
  host.innerHTML = `<div class="mb-s2 flex items-center gap-s3">
      <h4 class="m-0 font-heading text-subtab font-normal text-ink">Journal · <span class="tnum">${entries.length}</span></h4>
      ${promoted ? `<button data-act="inv-journal-publish" type="button" ${toSend ? '' : 'disabled'}
        class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60"
        >Publish my entries${toSend ? ` · ${toSend}` : ''}</button>` : ''}
      <span data-journal-publish-note class="text-caveat text-ink-muted"></span>
    </div>
    <p class="mb-s2 text-caveat text-ink-muted">${esc(where)}${entries.some((e) => e.note)
      ? ' An entry’s note goes in the same Egeria Note, after the entry.' : ''}</p>
    ${entries.length ? entries.map((e, i) => `
      <div class="mb-s2 rounded-sm border border-rule p-s2">
        <div class="text-caveat text-ink-muted">${esc(e.author)} · ${esc(String(e.written_at).slice(0, 10))}${
          e.compile_id ? ` · compile ${esc(String(e.compile_id).slice(0, 8))}` : ''}${
          words[e.id] ? ` · ${esc(words[e.id])}` : ''}</div>
        <div data-journal-body class="min-w-0 break-words text-answer text-ink">${bodies[i]}</div>
        ${e.note ? `<div data-journal-note class="mt-s1 text-caveat text-ink-muted">your note: ${esc(e.note)}</div>` : ''}
      </div>`).join('')
      : '<p class="text-caveat text-ink-muted">No entries yet. Use “add to investigation journal” under a chat answer.</p>'}`;
  host.querySelector('[data-act="inv-journal-publish"]')?.addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    const note = host.querySelector('[data-journal-publish-note]');
    btn.disabled = true;
    note.textContent = 'Publishing…';
    try {
      const res = await publishInvestigationJournal(inv.slug);
      const bad = (res.not_permitted || []).length + (res.unread || []).length;
      if (res.refused) note.textContent = res.refused;
      await loadJournal(inv, stale);
      if (bad) {
        const n = host.querySelector('[data-journal-publish-note]');
        if (n) n.textContent = `${bad} not sent — see each entry's state.`;
      }
    } catch (err) {
      btn.disabled = false;
      note.textContent = err.loginRequired ? 'Sign in to publish.' : `Not published: ${err.message}`;
    }
  });
}

/** A write failed: a pop-up with the reason (Escape or the backdrop closes it). The button carries its own cue. */
function failurePopup(title, reason) {
  const el = openDialog(title, '');
  const body = el.querySelector('#wl-detail-body');
  body.setAttribute('data-inv-failure-popup', '');
  body.innerHTML = `<p class="text-state-warn">${esc(reason || 'no reason given')}</p>`;
}

// Link status per slug: the counts from GET /link-status (read on every detail render), or null when that read
// failed. `_unlinked` is the fallback from the last promote/relink result. Unknown never shows as "0".
const _linkStatus = {};
const _unlinked = {};
// Per-member link rows from the same /link-status response (null: not read).
const _linkMembers = {};

/** One member's link cell: a cue and a short word, the reason on hover. Linked / not linked / could not tell; a
 *  status that was never read is "not read", never "not linked". Exported for the tests. */
export function memberLinkCellHtml(inv, m, rows) {
  if (!inv.egeria_project_guid) return '<span data-inv-member-link="unbound" class="text-ink-muted" title="Not bound to an Egeria Project">—</span>';
  const r = Array.isArray(rows) ? rows.find((x) => x.entity_type === m.entity_type && x.entity_slug === m.entity_slug) : null;
  if (!r) return `<span data-inv-member-link="unread">${cue('not_established', 'not read', 'The link status could not be read for this member.')}</span>`;
  const why = r.reason || '';
  if (r.state === 'linked') return `<span data-inv-member-link="linked">${cue('measured', 'linked', why || 'Attached to the working set in Egeria.')}</span>`;
  if (r.state === 'not_linked') return `<span data-inv-member-link="not_linked">${cue('unrun', 'not linked', why || 'Not attached yet.')}</span>`;
  return `<span data-inv-member-link="could_not_tell">${cue('not_established', 'could not tell', why || 'The link could not be established either way.')}</span>`;
}

function linkStatusHtml(slug) {
  const c = _linkStatus[slug];
  const plural = (k) => `${k} member${k === 1 ? '' : 's'}`;
  if (c) {
    const parts = [];
    if (c.not_linked > 0) {
      parts.push(`<span data-inv-not-linked class="ml-s2 text-state-warn">${plural(c.not_linked)} not linked</span>`);
    }
    if (c.could_not_tell > 0) {
      parts.push(`<span data-inv-link-unknown class="ml-s2 text-ink-muted">could not tell for ${plural(c.could_not_tell)}</span>`);
    }
    // linkable comes from link-status (the members relink would attach); an older server lacks it.
    const linkable = c.linkable ?? c.not_linked;
    return { html: parts.join(''), n: linkable, counted: c.linkable != null };
  }
  const n = _unlinked[slug];
  if (n > 0) {
    return { html: `<span data-inv-not-linked class="ml-s2 text-state-warn">${plural(n)} not linked</span>`, n };
  }
  return { html: `<span data-inv-link-unknown class="ml-s2 text-ink-muted">link status unavailable</span>`, n };
}

function egeriaSectionHtml(inv) {
  if (inv.egeria_project_guid) {
    const { html: notLinked, n, counted } = linkStatusHtml(inv.slug);
    return `<p class="max-w-[70ch] text-caveat text-ink-muted">
        <span data-inv-bound-cue class="text-state-ok" title="bound to an Egeria Project">● bound</span>
        · <span class="font-mono">${esc(inv.egeria_project_qualified_name || inv.egeria_project_guid)}</span>${notLinked}
      </p>
      <div class="flex flex-wrap gap-s2">
        <button data-act="inv-sync" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60">Publish again</button>
        <button data-act="inv-relink" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60">${n > 0 ? `Link members${counted ? ` (${n})` : ''}` : 'Relink members'}</button>
        <button data-act="inv-unbind" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60">Unbind</button>
      </div>`;
  }
  return `<p class="max-w-[70ch] text-caveat text-ink-muted">
      No Egeria Project yet — local only. Bind an existing one, or create the Egeria side
      (Project + Folio + ResourceList) from this investigation's current members.
    </p>
    <div class="flex flex-wrap gap-s2">
      <button data-act="inv-bind" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60">Bind existing project…</button>
      <button data-act="inv-promote" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60">Publish to Egeria →</button>
    </div>`;
}

const ENTITY_TYPES = [['repo', 'Repo'], ['database', 'Database'], ['filesystem', 'Filesystem']];

// The add-member slug field is an autocomplete, not a hard picker (a member
// can be added by slug even if its list hasn't loaded), but the datalist
// must actually offer the right resources for whichever type is selected --
// it used to always list `state.projects` (repos) regardless, so choosing
// "Database"/"Filesystem" left the field with no suggestions at all and
// nothing telling the user why. `ensureResourceListLoaded` (app.js) is the
// same lazy-fetch-and-cache the sidebar's own type switcher uses, keyed by
// its 'db'/'filesystem'/'repo' vocabulary -- not the backend's 'database',
// which is what ENTITY_TYPES (and the API call) correctly use instead.
function slugListSourceType(entityType) {
  return entityType === 'database' ? 'db' : entityType;
}

function slugListRows(entityType) {
  if (entityType === 'database') return state.databases || [];
  if (entityType === 'filesystem') return state.filesystems || [];
  return state.projects || [];
}

function addMemberFormHtml() {
  return `<div class="mb-s3 flex flex-wrap items-center gap-s2">
    <select id="inv-add-type" class="rounded-sm border border-rule bg-paper px-2 py-1 text-caveat text-ink">
      ${ENTITY_TYPES.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}
    </select>
    <input id="inv-add-slug" list="inv-add-slug-list" type="text" placeholder="slug"
      class="rounded-sm border border-rule bg-paper px-2 py-1 text-caveat text-ink">
    <datalist id="inv-add-slug-list">
      ${slugListRows('repo').map((p) => `<option value="${esc(p.slug)}">`).join('')}
    </datalist>
    <input id="inv-add-rationale" type="text" placeholder="why (optional)"
      class="flex-1 min-w-[160px] rounded-sm border border-rule bg-paper px-2 py-1 text-caveat text-ink">
    <button data-act="inv-add-member" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60">Add</button>
    <span id="inv-add-error" class="text-caveat text-state-warn"></span>
  </div>`;
}

function membersTableHtml(inv, members, dispositions) {
  if (!members.length) return '<p class="text-caveat text-ink-muted">No members yet.</p>';
  // dispositions is {disposition: [members]} -- flip to a lookup by
  // (entity_type, entity_slug) so each row can show its own, since the
  // members list itself does not carry disposition.
  const byKey = new Map();
  for (const [disp, list] of Object.entries(dispositions || {})) {
    for (const m of list || []) byKey.set(`${m.entity_type}/${m.entity_slug}`, disp);
  }
  return `<table class="w-full text-left">
    <thead><tr class="border-b border-rule text-caps uppercase tracking-caps text-ink-muted">
      <th class="pb-s2 pr-s3 font-normal">Type</th>
      <th class="pb-s2 pr-s3 font-normal">Resource</th>
      <th class="pb-s2 pr-s3 font-normal">Disposition</th>
      <th class="pb-s2 pr-s3 font-normal">In the working set</th>
      <th class="pb-s2 font-normal"></th>
    </tr></thead>
    <tbody>${members.map((m) => {
      const key = `${m.entity_type}/${m.entity_slug}`;
      const current = byKey.get(key) || '';
      return `<tr class="border-b border-rule">
        <td class="py-s2 pr-s3 text-caveat text-ink-muted">${esc(m.entity_type)}</td>
        <td class="py-s2 pr-s3 text-answer text-ink">${esc(m.entity_slug)}</td>
        <td class="py-s2 pr-s3 text-caveat">
          <select data-disposition="${esc(m.entity_type)}|${esc(m.entity_slug)}"
            class="rounded-sm border border-rule bg-paper px-1 py-[2px] text-caveat text-ink">
            <option value="" ${current ? '' : 'selected'}>◌ undecided</option>
            ${Object.entries(DISPOSITION_GLYPH).map(([d, g]) => `<option value="${d}" ${
              current === d ? 'selected' : ''}>${g} ${d}</option>`).join('')}
          </select>
        </td>
        <td class="py-s2 pr-s3 text-caveat" data-inv-member-link-cell>${memberLinkCellHtml(inv, m, _linkMembers[inv.slug])}</td>
        <td class="py-s2">
          <button data-remove-member="${esc(m.entity_type)}|${esc(m.entity_slug)}"
            class="cursor-pointer bg-transparent text-caveat text-state-warn underline">Remove</button>
        </td>
      </tr>`;
    }).join('')}</tbody>
  </table>`;
}

function bindDetail(inv, members) {
  const el = $('content');
  const back = () => { _detailSlug = null; renderInvestigation(); };
  el.querySelectorAll('[data-act="inv-back"]').forEach((b) => b.addEventListener('click', back));

  // Scope's "＋ add…": the ways to put resources in scope other than the slug
  // form below. "from a file" opens the Find dialog on its From-a-file tab with
  // THIS investigation already chosen as the destination: one dialog, two doors.
  el.querySelector('[data-act="inv-add-menu"]')?.addEventListener('click', () => {
    const host = $('inv-add-menu');
    if (host.innerHTML.trim()) { host.innerHTML = ''; return; }
    host.innerHTML = `<div class="mb-s2 flex flex-wrap items-center gap-s3 text-caveat" data-add-menu>
      <button data-act="inv-add-from-file" type="button"
        class="cursor-pointer bg-transparent text-accent-ink underline">from a file</button>
      <span class="text-ink-muted">or by slug, in the form below</span>
    </div>`;
    host.querySelector('[data-act="inv-add-from-file"]').addEventListener('click', async () => {
      host.innerHTML = '';
      // Imported on use: the Find dialog imports this module (to refresh the
      // open page after a confirm), and a static import here would be a cycle.
      const mod = await import('/static/next/db-server-discovery.js');
      mod.openFindDbServersDialog({ tab: 'file', investigation: inv.slug });
    });
  });
  el.querySelector('[data-act="inv-export-scope"]')?.addEventListener('click', async () => {
    const note = $('inv-scope-note');
    try {
      const { text, filename } = await fetchScopeCsv(inv.slug);
      saveCsv(text, filename);
      note.textContent = `Downloaded ${filename}.`;
    } catch (err) {
      note.textContent = `Could not export: ${err.message}`;
    }
  });

  el.querySelector('[data-act="inv-make-current"]')?.addEventListener('click', async () => {
    await setInvestigation(inv.slug);
    renderDetail(inv.slug);
  });

  el.querySelector('[data-act="inv-edit"]')?.addEventListener('click', async () => {
    const { purposeVocab } = await investigationVocab();
    const host = $('inv-edit-form');
    host.innerHTML = `<div class="mb-s3 rounded-sm border border-rule p-s2">
      <label class="mb-[3px] block text-caveat text-ink-muted">Name</label>
      <input id="inv-edit-name" type="text" value="${esc(inv.display_name || '')}"
        class="mb-s2 w-full rounded-sm border border-rule bg-paper px-2 py-1 text-answer text-ink">
      <label class="mb-[3px] block text-caveat text-ink-muted">Description</label>
      <textarea id="inv-edit-desc" rows="2"
        class="mb-s2 w-full rounded-sm border border-rule bg-paper px-2 py-1 text-caveat text-ink">${esc(inv.description || '')}</textarea>
      <label class="mb-[3px] block text-caveat text-ink-muted">Purposes</label>
      <div class="mb-s2">${purposePickerHtml({
        prefix: 'inv-edit', vocab: purposeVocab, selected: inv.purposes || [], text: inv.purpose_text || '' })}</div>
      <div class="flex gap-s2">
        <button id="inv-edit-save" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60">Save</button>
        <button id="inv-edit-cancel" class="cursor-pointer bg-transparent text-caveat text-ink-muted underline">Cancel</button>
      </div>
      <span id="inv-edit-error" class="text-caveat text-state-warn"></span>
    </div>`;
    wirePurposePicker(host, 'inv-edit');
    host.querySelector('#inv-edit-cancel').addEventListener('click', () => { host.innerHTML = ''; });
    host.querySelector('#inv-edit-save').addEventListener('click', async () => {
      const btn = host.querySelector('#inv-edit-save');
      btn.disabled = true;
      try {
        await updateInvestigation(inv.slug, {
          display_name: host.querySelector('#inv-edit-name').value.trim(),
          description: host.querySelector('#inv-edit-desc').value.trim(),
          ...readPurposePicker(host, 'inv-edit'),
        });
        await refreshInvestigationsAndSidebar();
        renderDetail(inv.slug);
      } catch (err) {
        btn.disabled = false;
        host.querySelector('#inv-edit-error').textContent = err.message;
      }
    });
  });

  el.querySelector('[data-act="inv-reclassify"]')?.addEventListener('click', async () => {
    const { classifications } = await investigationVocab();
    const host = $('inv-reclass-form');
    host.innerHTML = `<div class="mb-s3 rounded-sm border border-rule p-s2">
      <label class="mb-[3px] block text-caveat text-ink-muted">New kind</label>
      <select id="inv-reclass-select" class="mb-s2 w-full rounded-sm border border-rule bg-paper px-2 py-1 text-caveat text-ink">
        ${classifications.classifications.map((c) => `<option value="${esc(c.name)}" ${
          c.name === inv.project_classification ? 'selected' : ''}>${esc(c.label || c.name)}</option>`).join('')}
      </select>
      <div id="inv-reclass-hyp-wrap" class="mb-s2 hidden">
        <label class="mb-[3px] block text-caveat text-ink-muted">Hypothesis</label>
        <input id="inv-reclass-hyp" type="text" value="${esc(inv.hypothesis || '')}"
          class="w-full rounded-sm border border-rule bg-paper px-2 py-1 text-caveat text-ink">
      </div>
      <p class="mb-s2 text-caveat text-ink-muted">
        Loosening visibility (e.g. Personal/Experiment → a public kind) always works. Tightening
        can fail partway if artifacts already reached a public Egeria zone — the result will say
        exactly which elements moved and which did not.
      </p>
      <div class="flex gap-s2">
        <button id="inv-reclass-save" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60">Reclassify</button>
        <button id="inv-reclass-cancel" class="cursor-pointer bg-transparent text-caveat text-ink-muted underline">Cancel</button>
      </div>
      <div id="inv-reclass-result" class="mt-s2 text-caveat"></div>
    </div>`;
    const sel = host.querySelector('#inv-reclass-select');
    const hypWrap = host.querySelector('#inv-reclass-hyp-wrap');
    const syncHyp = () => hypWrap.classList.toggle('hidden',
      !classifications.classifications.find((c) => c.name === sel.value)?.requires_hypothesis);
    sel.addEventListener('change', syncHyp);
    syncHyp();
    host.querySelector('#inv-reclass-cancel').addEventListener('click', () => { host.innerHTML = ''; });
    host.querySelector('#inv-reclass-save').addEventListener('click', async () => {
      const btn = host.querySelector('#inv-reclass-save');
      btn.disabled = true;
      btn.textContent = 'Reclassifying…';
      const resultEl = host.querySelector('#inv-reclass-result');
      try {
        // ReclassificationResult.as_dict() (investigation_reclassifier.py):
        // `still_public` is a LIST of guids still exposed on a tightening
        // (always [] otherwise), `local_applied`/`ok` are booleans. Left on
        // screen with an explicit "Reload" rather than an immediate
        // renderDetail() -- this is the one write in the whole pane where a
        // partial result naming exactly which elements did not move matters
        // more than snapping back to a clean view.
        const result = await reclassifyInvestigation(
          inv.slug, sel.value, host.querySelector('#inv-reclass-hyp').value.trim());
        btn.textContent = 'Reclassify';
        resultEl.innerHTML = (result.ok
          ? `<span class="text-state-ok">Reclassified to ${esc(result.to_classification)}.</span>`
          : `<span class="text-state-warn">Partial (${esc(result.direction)}): local_applied=${
              esc(String(result.local_applied))}.${result.still_public.length
                ? ` Still public: ${result.still_public.map(esc).join(', ')}.` : ''}${
              result.errors.length ? ` Errors: ${result.errors.map(esc).join('; ')}.` : ''}</span>`)
          + `<div class="mt-s2"><button id="inv-reclass-reload" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60">Reload</button></div>`;
        resultEl.querySelector('#inv-reclass-reload').addEventListener('click', () => renderDetail(inv.slug));
        await refreshInvestigationsAndSidebar();
      } catch (err) {
        btn.disabled = false;
        btn.textContent = 'Reclassify';
        resultEl.innerHTML = refusalHtml(err);
      }
    });
  });

  // Delete: the confirm and its choices, the call, and the cue that stays when Egeria stops it (investigation-delete.js).
  showRememberedStop(inv, $('inv-delete-result'));
  el.querySelector('[data-act="inv-delete"]')?.addEventListener('click', (e) => startDelete(inv, e.currentTarget, {
    host: $('inv-delete-result'),
    failurePopup,
    back: async ({ note }) => {
      // Gone: leave the page, stop treating it as current, and land on the list with one line saying what happened.
      if (state.investigation === inv.slug) await setInvestigation('');
      await refreshInvestigationsAndSidebar();
      _detailSlug = null;
      await renderInvestigation();
      if (note) {
        $('content').insertAdjacentHTML('afterbegin',
          `<p data-inv-deleted-note class="mb-s2 max-w-[80ch] text-caveat text-state-ok">${note}</p>`);
      }
    },
  }));

  el.querySelector('[data-act="inv-close"]')?.addEventListener('click', async () => {
    if (!(await inPageConfirm({ title: 'Close investigation?', message: `Close '${inv.display_name || inv.slug}'? Its findings stay queryable; it just drops out of the default scope. Nothing is deleted.`, confirmLabel: 'Close' }))) return;
    await closeInvestigation(inv.slug);
    await refreshInvestigationsAndSidebar();
    renderDetail(inv.slug);
  });
  el.querySelector('[data-act="inv-suspend"]')?.addEventListener('click', async () => {
    await suspendInvestigation(inv.slug);
    await refreshInvestigationsAndSidebar();
    renderDetail(inv.slug);
  });
  el.querySelector('[data-act="inv-reopen"]')?.addEventListener('click', async () => {
    await reopenInvestigation(inv.slug);
    await refreshInvestigationsAndSidebar();
    renderDetail(inv.slug);
  });

  el.querySelector('[data-act="inv-bind"]')?.addEventListener('click', () => {
    const host = $('inv-egeria-form');
    host.innerHTML = `<div class="mt-s2 rounded-sm border border-rule p-s2">
      <label class="mb-[3px] block text-caveat text-ink-muted">Egeria Project GUID</label>
      <input id="inv-bind-guid" type="text" class="mb-s2 w-full rounded-sm border border-rule bg-paper px-2 py-1 text-caveat text-ink">
      <label class="mb-[3px] block text-caveat text-ink-muted">Qualified name (optional)</label>
      <input id="inv-bind-qn" type="text" class="mb-s2 w-full rounded-sm border border-rule bg-paper px-2 py-1 text-caveat text-ink">
      <div class="flex gap-s2">
        <button id="inv-bind-save" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60">Bind</button>
        <button id="inv-bind-cancel" class="cursor-pointer bg-transparent text-caveat text-ink-muted underline">Cancel</button>
      </div>
      <span id="inv-bind-error" class="text-caveat text-state-warn"></span>
    </div>`;
    host.querySelector('#inv-bind-cancel').addEventListener('click', () => { host.innerHTML = ''; });
    host.querySelector('#inv-bind-save').addEventListener('click', async () => {
      const guid = host.querySelector('#inv-bind-guid').value.trim();
      if (!guid) { host.querySelector('#inv-bind-error').textContent = 'GUID is required.'; return; }
      const btn = host.querySelector('#inv-bind-save');
      btn.disabled = true;
      try {
        await bindInvestigationEgeriaProject(inv.slug, {
          status: 'linked', egeriaProjectGuid: guid,
          egeriaProjectQualifiedName: host.querySelector('#inv-bind-qn').value.trim(),
        });
        await refreshInvestigationsAndSidebar();
        renderDetail(inv.slug);
      } catch (err) {
        btn.disabled = false;
        host.querySelector('#inv-bind-error').innerHTML = refusalHtml(err);
      }
    });
  });

  el.querySelector('[data-act="inv-unbind"]')?.addEventListener('click', async () => {
    if (!(await inPageConfirm({ title: 'Unbind?', message: 'Unbind this investigation from its Egeria Project? The Egeria Project itself is not deleted.', confirmLabel: 'Unbind' }))) return;
    await bindInvestigationEgeriaProject(inv.slug, { status: 'unset' });
    await refreshInvestigationsAndSidebar();
    renderDetail(inv.slug);
  });

  el.querySelector('[data-act="inv-promote"]')?.addEventListener('click', async () => {
    const btn = el.querySelector('[data-act="inv-promote"]');
    btn.disabled = true;
    btn.textContent = 'Creating…';
    const host = $('inv-egeria-form');
    try {
      let result;
      try {
        result = await promoteInvestigation(inv.slug);
      } catch (err) {
        // The write failed: reset the button, keep a cue on it, and say why in a pop-up.
        btn.disabled = false;
        if (err.status === 403) btn.innerHTML = cue('no_access', 'not permitted', err.message);
        else {
          btn.innerHTML = `Publish to Egeria → ${cue('error', 'not published', err.message)}`;
          failurePopup('Not published', err.message);
        }
        host.innerHTML = `<div class="mt-s2 text-caveat" data-inv-promote-refused>${refusalHtml(err)}</div>`;
        return;
      }
      // The write is done: the button is reset now, whatever the re-read below does.
      btn.disabled = false;
      btn.textContent = 'Publish to Egeria →';
      const confirmedLine = result.classification_confirmed === result.classification_requested
        ? `confirmed as ${esc(result.classification_confirmed)}`
        : result.classification_confirmed === null || result.classification_confirmed === undefined
          ? 'could not verify the classification took'
          : `requested ${esc(result.classification_requested)}, Egeria reports ${esc(result.classification_confirmed)}`;
      host.innerHTML = `<div class="mt-s2 text-caveat ${result.ok ? 'text-state-ok' : 'text-state-warn'}">
        ${result.ok ? (result.classification_confirmed != null ? `published · ${readBackAt()}` : 'sent · waiting for Egeria') : 'Partial — see below.'} ${confirmedLine}.
        ${result.members_linked?.length ? ` Linked: ${result.members_linked.map(esc).join(', ')}.` : ''}
        ${result.members_unlinkable?.length ? ` Could not link: ${result.members_unlinkable.map(memberLine).join('; ')}.` : ''}
        ${result.errors?.length ? ` Errors: ${result.errors.map(esc).join('; ')}.` : ''}
        <div class="mt-s2"><button id="inv-promote-reload" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60">Reload</button></div>
      </div>`;
      // The result stays on screen (a partial promote's members_unlinkable/errors matter more than a clean
      // view), but the section above is redrawn as bound -- the 'Creating…' button must not outlive the write.
      const resultHtml = host.innerHTML;
      _unlinked[inv.slug] = result.members_unlinkable?.length ?? 0;
      await refreshInvestigationsAndSidebar();
      await renderDetail(inv.slug);
      const host2 = $('inv-egeria-form');
      if (host2) {
        host2.innerHTML = resultHtml;
        host2.querySelector('#inv-promote-reload')?.addEventListener('click', () => renderDetail(inv.slug));
      }
    } catch (err) {
      // Only the re-read after a finished write lands here: the result stays on screen, the button is reset.
      btn.disabled = false;
      btn.textContent = 'Publish to Egeria →';
      host.insertAdjacentHTML('beforeend', `<div class="mt-s2 text-caveat" data-inv-promote-refused>${refusalHtml(err)}</div>`);
    }
  });

  el.querySelector('[data-act="inv-sync"]')?.addEventListener('click', async () => {
    const btn = el.querySelector('[data-act="inv-sync"]');
    btn.disabled = true;
    btn.textContent = 'Publishing…';
    try {
      await syncInvestigationEgeria(inv.slug);
      btn.disabled = false;
      btn.textContent = 'Sent · waiting for Egeria';
      setTimeout(() => { btn.textContent = 'Publish again'; }, 3000);
    } catch (err) {
      btn.disabled = false;
      if (err.status === 403) btn.innerHTML = cue('no_access', 'not permitted', err.message);
      else btn.textContent = `Not published: ${err.message}`;
    }
  });

  el.querySelector('[data-act="inv-relink"]')?.addEventListener('click', async () => {
    const btn = el.querySelector('[data-act="inv-relink"]');
    btn.disabled = true;
    btn.innerHTML = cue('running', 'linking…');
    try {
      const res = await relinkInvestigationMembers(inv.slug);
      btn.disabled = false;
      if (res && Array.isArray(res.members_unlinkable)) _unlinked[inv.slug] = res.members_unlinkable.length;
      const linkedN = Array.isArray(res?.members_linked) ? res.members_linked.length : 0;
      const errs = Array.isArray(res?.errors) ? res.errors : [];
      await renderDetail(inv.slug);
      const host0 = $('inv-egeria-form');
      if (host0) {
        host0.innerHTML = `<div class="mt-s2 text-caveat" data-inv-relink-result>${cue('measured', `${linkedN} linked`)}</div>`;
      }
      if (errs.length) failurePopup('Some members were not linked', errs.join('; '));
      // Brief Z3: members the check refused stay named under the button, each with the cue.
      const refused = (res?.members_unlinkable || []).filter((m) => String(m.reason || '').startsWith('not permitted'));
      const host = $('inv-egeria-form');
      if (refused.length && host) {
        host.insertAdjacentHTML('beforeend', `<div class="mt-s2 text-caveat" data-inv-relink-refused>${refused.map(memberLine).join('; ')}</div>`);
      }
    } catch (err) {
      btn.disabled = false;
      if (err.status === 403) btn.innerHTML = cue('no_access', 'not permitted', err.message);
      else {
        btn.innerHTML = `Link members ${cue('error', 'not linked', err.message)}`;
        failurePopup('Not linked', err.message);
      }
    }
  });

  el.querySelector('#inv-add-type')?.addEventListener('change', async (e) => {
    const entityType = e.target.value;
    await ensureResourceListLoaded(slugListSourceType(entityType));
    const list = el.querySelector('#inv-add-slug-list');
    if (list) {
      list.innerHTML = slugListRows(entityType).map((p) => `<option value="${esc(p.slug)}">`).join('');
    }
  });

  el.querySelector('[data-act="inv-add-member"]')?.addEventListener('click', async () => {
    const entityType = el.querySelector('#inv-add-type').value;
    const entitySlug = el.querySelector('#inv-add-slug').value.trim();
    const rationale = el.querySelector('#inv-add-rationale').value.trim();
    const errEl = el.querySelector('#inv-add-error');
    if (!entitySlug) { errEl.textContent = 'Slug is required.'; return; }
    try {
      await addInvestigationMember(inv.slug, entityType, entitySlug, rationale);
      if (inv.slug === state.investigation) {
        // Keep the sidebar's own working-set cache (used for "In scope"
        // filtering) in step, same reasoning as bulkScope() in app.js.
        await refreshInvestigationsAndSidebar();
      }
      renderDetail(inv.slug);
    } catch (err) {
      errEl.textContent = err.message;
    }
  });

  el.querySelectorAll('[data-remove-member]').forEach((b) => b.addEventListener('click', async () => {
    const [entityType, entitySlug] = b.dataset.removeMember.split('|');
    if (!(await inPageConfirm({ title: 'Remove from scope?', message: `Remove ${entitySlug} from this investigation's scope?`, confirmLabel: 'Remove' }))) return;
    try {
      await removeInvestigationMember(inv.slug, entityType, entitySlug);
      if (inv.slug === state.investigation) await refreshInvestigationsAndSidebar();
      renderDetail(inv.slug);
    } catch (err) {
      inlineNote(b, `not removed: ${err.message}`);
    }
  }));

  el.querySelectorAll('[data-disposition]').forEach((sel) => sel.addEventListener('change', async () => {
    const [entityType, entitySlug] = sel.dataset.disposition.split('|');
    const disposition = sel.value;
    sel.disabled = true;
    try {
      await setInvestigationDisposition(inv.slug, entityType, entitySlug, disposition);
    } catch (err) {
      inlineNote(sel, `not saved: ${err.message}`);
    } finally {
      sel.disabled = false;
    }
  }));

  el.querySelectorAll('[data-next-step]').forEach((b) => b.addEventListener('click', () => {
    const action = b.dataset.nextStep;
    if (action === 'egeria') { $('inv-egeria-form').scrollIntoView({ behavior: 'smooth' }); return; }
    if (action === 'disposition') { el.querySelector('table:last-of-type')?.scrollIntoView({ behavior: 'smooth' }); return; }
    if (action === 'edit') { el.querySelector('[data-act="inv-edit"]')?.click(); return; }
    // 'scouting' -- and anything else unrecognised -- has no in-pane target;
    // naming it beats a button that silently does nothing.
    inlineNote(b, `This step points at ${action}, which this pane does not deep-link to yet.`);
  }));
}
