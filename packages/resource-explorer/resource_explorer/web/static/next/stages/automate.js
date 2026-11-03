/* Automate — the 8th intent: local-first notification subscriptions plus
 * the global Schedules overview (packages/resource-explorer/CLAUDE.md's
 * Automate section; docs/discovery-automate-project-context-plan.md Part 4).
 *
 * PLAN-FINISH-REPOS.md item 4 asked for either a real port of the classic
 * `index.html` Automate view or an honest defer. This is a real, DELIBERATELY
 * PARTIAL port:
 *
 *   - VIEWING and TOGGLING subscriptions (🔔 Subscriptions) is built here,
 *     against the same `/api/automate/subscriptions` routes classic uses.
 *   - VIEWING and DELETING the global Schedules overview (⏱ Schedules) is
 *     built here too, against `/api/schedules/`.
 *   - CREATING a subscription is now built too (re/next-automate-create-
 *     subscription), but NOT in this file -- the correction below is worth
 *     spelling out because the paragraph this replaced said the opposite,
 *     and a future reader trusting the old claim would go looking for a
 *     card grid that was never going to exist.
 *
 *     Classic creates one from a "🔔 Notify me" button on an Assessment/
 *     Analysis CARD (index.html, `_createSubscriptionFromCard` /
 *     "Cross-stage definitions") -- a card names exactly one analysis_id, so
 *     classic's button fires the POST directly with no form. Assessment and
 *     Analysis are built in /next (item 11) through the generic Questions-
 *     checklist engine -- QUESTION ROWS, not a card grid, and that card grid
 *     still has no /next equivalent and was never going to get one (building
 *     a standalone form detached from a card that doesn't exist would have
 *     recreated the exact half-built-feature outcome PLAN-FINISH-REPOS.md's
 *     own stub comment warns against).
 *
 *     The real attachment point turned out to be the question row itself:
 *     `app.js`'s `provenanceLine()` grows a "🔔 notify me" action wherever a
 *     row carries `analysis_ids` (`bindRowActions` wires it to
 *     `openNotifyDialog()`, also in app.js). One further wrinkle a card
 *     never had: a question's `analysis_ids` is not always 1:1 with one
 *     analysis (a MIXED:/PARTIAL: answer can name several), so unlike
 *     classic's one-click button, `openNotifyDialog()` always opens a small
 *     dialog (`worklist.js`'s `openDialog()`) -- a picker when the row names
 *     more than one analysis, a plain confirm when it names exactly one --
 *     rather than ever guessing which one the reader meant. It posts the
 *     same `/api/automate/subscriptions` this view reads (via `re-api.js`'s
 *     new `createSubscription()`), so a subscription made from a question
 *     row shows up in the table below identically to one classic made from
 *     a card, with no separate plumbing needed: this view already re-fetches
 *     `listSubscriptions()` on every render rather than caching, so there is
 *     nothing here to push into.
 *
 *     The repo-only gate this comment used to cite
 *     (`state.resourceType !== 'repo'` on the Questions engine's own
 *     `loadPane()`) is gone as of the database/filesystem generalization --
 *     `openNotifyDialog()` now sends `apiEntityType(state.resourceType)`
 *     rather than a hardcoded 'repo', so a database/filesystem question row
 *     subscribes correctly too (found broken live 2026-09-28: it was still
 *     hardcoding 'repo' after the gate lifted, which 404'd on the server's
 *     repo-only lookup for a database slug).
 *
 * Like Understanding (next/stages/understanding.js), Automate bypasses the
 * generic Questions-checklist engine entirely -- `loadPane()` in app.js
 * calls `renderAutomate()` directly for `state.stage === 'automate'` and
 * returns. It also bypasses the standard SUB_TABS row: classic's Automate
 * view has its own two-tab subnav (Subscriptions / Schedules), unrelated to
 * Questions/Survey/By analysis/Disposition, and showing that strip here
 * would offer four tabs that mean nothing for this stage.
 */
import { ago } from '/static/next/format.js';
import { inPageConfirm } from '/static/next/confirm-dialog.js';
import {
  listSubscriptions, setSubscriptionActive, listAllSchedules, deleteSchedule, runScheduleNow,
  saveSchedule, getActivityEntry, listAnalyses, listSurveyDefinitions,
} from '/static/re-api.js';
import { readableDetailHtml } from '/static/next/stages/activity.js';
import { state, esc, $, apiEntityType } from '/static/next/app.js';
import { openFirstIndexAsk, refreshFirstIndex, subscribeFirstIndex } from '/static/next/first-index.js';
import { ordinal } from '/static/next/stages/repo-header.js';

// Module-local, not on `state`: which of Automate's two sub-tabs is showing,
// and whether the subscriptions list is filtered to the selected resource.
// Mirrors classic's `_automateSubTab`/`_automateFilterToSelected` -- this is
// view state for one pane, not something any other stage reads.
let _tab = 'subscriptions';
let _filterToSelected = false;

function subnavHtml() {
  const tabs = [
    ['subscriptions', '🔔 Subscriptions'],
    ['schedules', '⏱ Schedules'],
    ['first-index', 'First indexing'],
  ];
  return `<div class="mb-s4 flex items-center gap-s4 font-heading text-subtab text-ink">
    ${tabs.map(([id, label]) => `
      <button data-automate-tab="${id}"
        class="cursor-pointer border-0 bg-transparent pb-[2px] ${
          _tab === id ? 'border-b border-accent text-ink' : 'text-ink-muted hover:text-ink'}"
        >${esc(label)}</button>`).join('')}
    <span class="ml-auto text-caps uppercase tracking-caps text-ink-muted">Automate</span>
  </div>`;
}

function bindSubnav(rerender) {
  $('content').querySelectorAll('[data-automate-tab]').forEach((b) =>
    b.addEventListener('click', () => { _tab = b.dataset.automateTab; rerender(); }));
}

export async function renderAutomate() {
  if (_tab === 'schedules') return renderSchedules();
  if (_tab === 'first-index') return renderFirstIndexing();
  return renderSubscriptions();
}

/* ── 🔔 Subscriptions ──────────────────────────────────────────────────── */

async function renderSubscriptions() {
  const el = $('content');
  el.innerHTML = `${subnavHtml()}<p class="text-answer text-ink-muted">Loading…</p>`;
  bindSubnav(renderAutomate);

  const slug = state.selectedSlug;
  let subs;
  try {
    subs = await listSubscriptions(
      _filterToSelected && slug ? { entityType: apiEntityType(state.resourceType), entitySlug: slug } : {});
  } catch (err) {
    el.innerHTML = `${subnavHtml()}
      <p class="max-w-[70ch] text-answer text-state-warn">Could not load subscriptions: ${esc(err.message)}</p>`;
    bindSubnav(renderAutomate);
    return;
  }

  const filterToggle = slug
    ? `<label class="ml-s3 inline-flex cursor-pointer items-center gap-[5px] text-caveat text-ink-muted">
         <input type="checkbox" data-automate-filter ${_filterToSelected ? 'checked' : ''}>
         Just <span class="font-mono">${esc(slug)}</span>
       </label>`
    : '';

  const rows = subs.map((s) => {
    // An active subscription with no recurring schedule for the same
    // analysis can never fire -- detection only runs off scheduled
    // completions (registry.py / scheduler._check_subscriptions). Said on
    // the row itself, not just at creation time, because the condition can
    // become true later (a schedule someone else removed).
    const status = !s.active
      ? '<span class="text-ink-muted">○ inactive</span>'
      : s.has_schedule === false
        ? `<span class="text-state-warn" title="Detection only runs off scheduled runs. Set a cadence for '${
            esc(s.analysis_id)}' on this resource in the ⏱ Schedules tab, or from the analysis card's ⏱ Schedule action."
            >● active — but never fires (no schedule)</span>`
        : '<span class="text-state-ok">● active</span>';
    const checked = s.last_checked_at ? `checked ${esc(ago(s.last_checked_at))}`
      : (s.active && s.has_schedule === false ? 'never — no schedule to run off' : 'never checked yet');
    const notified = s.notification_count > 0
      ? `🔔 notified <span class="tnum">${s.notification_count}</span>×${
          s.last_notified_at ? ` (last ${esc(ago(s.last_notified_at))})` : ''}`
      : '<span class="text-ink-muted">never notified</span>';
    return `<tr class="border-b border-rule">
      <td class="py-s2 pr-s3 text-answer text-ink">${esc(s.label || s.analysis_id)}</td>
      <td class="py-s2 pr-s3 text-caveat text-ink-muted">${esc(s.entity_type)}/${esc(s.entity_slug)}</td>
      <td class="py-s2 pr-s3 text-caveat text-ink-muted">${esc(s.analysis_id)}</td>
      <td class="py-s2 pr-s3 text-caveat">${status}</td>
      <td class="py-s2 pr-s3 text-caveat text-ink-muted" title="${esc(checked)}">${esc(checked)}</td>
      <td class="py-s2 pr-s3 text-caveat">${notified}</td>
      <td class="py-s2">
        <button data-toggle-sub="${s.id}" data-next-active="${s.active ? '0' : '1'}"
          class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px]
                 text-caveat text-ink hover:border-accent">${s.active ? 'Deactivate' : 'Activate'}</button>
      </td>
    </tr>`;
  }).join('');

  el.innerHTML = `${subnavHtml()}
    <div class="mb-s3 flex items-center">
      <h3 class="m-0 font-heading text-name font-normal text-ink">Subscriptions</h3>
      ${filterToggle}
    </div>
    <p class="max-w-[70ch] text-caveat text-ink-muted">
      A subscription watches an analysis for change on its <em>scheduled</em> runs (see ⏱ Schedules --
      one with no active schedule for its analysis never fires) and delivers via the RFA drawer when
      something changes. Create one from the "🔔 notify me" action on a question row in Assessment or
      Analysis${slug ? '' : ' (repositories only, for now)'}.
    </p>
    <div class="my-s3 h-px bg-rule"></div>
    ${subs.length ? `<table class="w-full text-left">
      <thead><tr class="border-b border-rule text-caps uppercase tracking-caps text-ink-muted">
        <th class="pb-s2 pr-s3 font-normal">Watching</th>
        <th class="pb-s2 pr-s3 font-normal">Resource</th>
        <th class="pb-s2 pr-s3 font-normal">Analysis</th>
        <th class="pb-s2 pr-s3 font-normal">Status</th>
        <th class="pb-s2 pr-s3 font-normal">Last checked</th>
        <th class="pb-s2 pr-s3 font-normal">Notifications</th>
        <th class="pb-s2 font-normal"></th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>` : `<p class="text-answer text-ink-muted">
      No subscriptions${_filterToSelected && slug ? ` for ${esc(slug)}` : ''} yet.</p>`}`;

  bindSubnav(renderAutomate);
  const filterBox = el.querySelector('[data-automate-filter]');
  if (filterBox) {
    filterBox.addEventListener('change', () => {
      _filterToSelected = filterBox.checked;
      renderSubscriptions();
    });
  }
  el.querySelectorAll('[data-toggle-sub]').forEach((b) => b.addEventListener('click', async () => {
    const id = b.dataset.toggleSub;
    const nextActive = b.dataset.nextActive === '1';
    b.disabled = true;
    b.textContent = 'Saving…';
    try {
      await setSubscriptionActive(id, nextActive);
      renderSubscriptions();
    } catch (err) {
      b.disabled = false;
      b.textContent = `Not saved: ${err.message}`;
    }
  }));
}

/* ── ⏱ Schedules ───────────────────────────────────────────────────────── */

const RESOURCE_ICON = { repo: '📁', database: '🗄', filesystem: '💾' };

async function renderSchedules() {
  const el = $('content');
  el.innerHTML = `${subnavHtml()}<p class="text-answer text-ink-muted">Loading…</p>`;
  bindSubnav(renderAutomate);

  let schedules;
  try {
    schedules = await listAllSchedules();
  } catch (err) {
    el.innerHTML = `${subnavHtml()}
      <p class="max-w-[70ch] text-answer text-state-warn">Could not load schedules: ${esc(err.message)}</p>`;
    bindSubnav(renderAutomate);
    return;
  }

  const rows = schedules.map((s) => {
    const key = `${s.entity_type}|${s.entity_slug}|${s.analysis_id}`;
    // An error is a control, not a glyph: pressing it reads the activity row the failed run wrote.
    const status = !s.last_run_status
      ? '<span class="text-ink-muted">— not yet run</span>'
      : s.last_run_status === 'error'
        ? `<button type="button" data-sched-error="${esc(key)}" data-activity-id="${esc(s.last_run_activity_id || '')}"
             aria-expanded="false" title="Why did the last run fail?"
             class="cursor-pointer border-0 bg-transparent p-0 text-caveat text-state-warn underline decoration-dotted">⚠ error — why?</button>`
        : '<span class="text-state-ok">✓ ok</span>';
    const kind = s.target_kind === 'survey' ? ' <span class="text-provenance text-ink-muted">· survey</span>' : '';
    return `<tr class="border-b border-rule" data-sched-row="${esc(key)}">
      <td class="py-s2 pr-s3 text-caveat text-ink">${RESOURCE_ICON[s.entity_type] || '•'} ${esc(s.entity_slug)}</td>
      <td class="py-s2 pr-s3 text-caveat text-ink-muted">${esc(s.analysis_id)}${kind}</td>
      <td class="py-s2 pr-s3 text-caveat text-ink-muted">${esc(s.schedule)}</td>
      <td class="py-s2 pr-s3 text-caveat">${enabledToggleHtml(s, key)}</td>
      <td class="py-s2 pr-s3 text-caveat">${status}</td>
      <td class="py-s2 pr-s3 text-caveat text-ink-muted">${esc((s.last_run || '—').replace('T', ' ').slice(0, 16))}</td>
      <td class="py-s2 pr-s3 text-caveat text-ink-muted">${esc((s.next_run || '—').replace('T', ' ').slice(0, 16))}</td>
      <td class="py-s2 pr-s3">
        <button data-run-sched="${esc(s.entity_type)}|${esc(s.entity_slug)}|${esc(s.analysis_id)}"
          title="Run this scheduled analysis now, through the same dispatch the timer uses. Does not change its next scheduled run."
          class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px]
                 text-caveat text-accent-ink">Run now</button>
      </td>
      <td class="py-s2">
        <button data-delete-sched="${esc(s.entity_type)}|${esc(s.entity_slug)}|${esc(s.analysis_id)}"
          title="Remove this schedule"
          class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px]
                 text-caveat text-state-warn hover:border-state-warn">Remove</button>
      </td>
    </tr>
    <tr data-sched-error-row="${esc(key)}" class="hidden"><td colspan="9" class="pb-s2"></td></tr>`;
  }).join('');

  el.innerHTML = `${subnavHtml()}
    <h3 class="m-0 font-heading text-name font-normal text-ink">Schedules overview</h3>
    <p class="max-w-[70ch] text-caveat text-ink-muted">
      Every scheduled analysis or survey across every resource -- what a 🔔 subscription actually needs
      to fire. A paused schedule keeps its cadence and does not run until it is switched on.
    </p>
    <div id="schedule-add" class="mt-s3">${scheduleFormHtml()}</div>
    <div class="my-s3 h-px bg-rule"></div>
    ${schedules.length ? `<table class="w-full text-left">
      <thead><tr class="border-b border-rule text-caps uppercase tracking-caps text-ink-muted">
        <th class="pb-s2 pr-s3 font-normal">Resource</th>
        <th class="pb-s2 pr-s3 font-normal">Analysis</th>
        <th class="pb-s2 pr-s3 font-normal">Cadence</th>
        <th class="pb-s2 pr-s3 font-normal">State</th>
        <th class="pb-s2 pr-s3 font-normal">Last run</th>
        <th class="pb-s2 pr-s3 font-normal">When</th>
        <th class="pb-s2 pr-s3 font-normal">Next run</th>
        <th class="pb-s2 pr-s3 font-normal"></th>
        <th class="pb-s2 font-normal"></th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>` : '<p class="text-answer text-ink-muted">No schedules yet.</p>'}`;

  bindSubnav(renderAutomate);
  bindScheduleForm(el, renderSchedules);
  bindScheduleStateAndErrors(el, schedules, renderSchedules);
  el.querySelectorAll('[data-run-sched]').forEach((b) => b.addEventListener('click', async () => {
    const [entityType, entitySlug, analysisId] = b.dataset.runSched.split('|');
    const original = b.textContent;
    b.disabled = true;
    // No timed "started" toast — classic's own runScheduleNow comment notes
    // some analyses run for minutes, and a toast that expires mid-run would
    // leave the same silent gap. The button IS the progress indicator.
    b.textContent = 'Running…';
    try {
      const result = await runScheduleNow(entityType, entitySlug, analysisId);
      const errs = result?.errors || [];
      b.textContent = errs.length ? `Ran with ${errs.length} error(s)` : 'Ran now';
      renderSchedules();
    } catch (err) {
      b.disabled = false;
      b.textContent = err.status === 404 ? 'No schedule' : `Not run: ${err.message}`;
      setTimeout(() => { b.textContent = original; }, 4000);
    }
  }));
  el.querySelectorAll('[data-delete-sched]').forEach((b) => b.addEventListener('click', async () => {
    const [entityType, entitySlug, analysisId] = b.dataset.deleteSched.split('|');
    if (!(await inPageConfirm({ title: 'Remove schedule?', message: `Remove the ${analysisId} schedule for ${entitySlug}?`, confirmLabel: 'Remove' }))) return;
    b.disabled = true;
    try {
      await deleteSchedule(entityType, entitySlug, analysisId);
      renderSchedules();
    } catch (err) {
      b.disabled = false;
      b.textContent = `Not removed: ${err.message}`;
    }
  }));
}


/* ── First indexing ────────────────────────────────────────────────────── */

const FI_WORD = { waiting: '◔ waiting', indexing: '◔ indexing', indexed: '✓ indexed', failed: '✕ failed' };
let _fiUnsub = null;

/** One line per repository that has said yes (waiting · indexing · indexed · failed), "N of M done", and the door
 *  to the batch ask for everything not yet indexed. Each state is a run row's; the list repaints as they change. */
async function renderFirstIndexing() {
  const el = $('content');
  el.innerHTML = `${subnavHtml()}<p class="text-answer text-ink-muted">Loading…</p>`;
  bindSubnav(renderAutomate);
  const fi = await refreshFirstIndex();
  if (_tab !== 'first-index') return;
  drawFirstIndexing(fi);
}

function drawFirstIndexing(fi) {
  const el = $('content');
  const notYet = (state.resourceType === 'repo' ? state.projects : []).filter((p) => p.index_state === 'not_yet_indexed');
  const list = fi.list || [];
  const rows = list.map((l) => `<tr class="border-b border-rule" data-fi-row="${esc(l.slug)}" data-fi-state="${esc(l.state)}">
      <td class="py-s2 pr-s3 text-answer text-ink">${esc(l.name)}</td>
      <td class="py-s2 pr-s3 text-caveat ${l.state === 'failed' ? 'text-state-warn' : l.state === 'indexed' ? 'text-state-ok' : 'text-ink-muted'}">
        ${esc(FI_WORD[l.state] || l.state)}${l.batch_total && l.position ? ` · ${ordinal(l.position)} of ${l.batch_total}` : ''}</td>
      <td class="py-s2 pr-s3 text-caveat text-ink-muted">${l.state === 'failed' ? esc(l.reason) : l.state === 'indexed' ? esc(String(l.finished_at || '').slice(0, 16).replace('T', ' ')) : ''}</td>
    </tr>`).join('');
  el.innerHTML = `${subnavHtml()}
    <h3 class="m-0 font-heading text-name font-normal text-ink">First indexing</h3>
    <p class="max-w-[70ch] text-caveat text-ink-muted">Repositories you said yes to indexing for chat, in the order of the yeses.
      Each line is the state of that repository's run.</p>
    <div class="mt-s3 flex items-center gap-s3">
      <span data-fi-count class="text-answer text-ink">${fi.of ? `${fi.done} of ${fi.of} done` : 'Nothing queued yet'}</span>
      ${notYet.length ? `<button type="button" data-fi-choose class="cursor-pointer border-0 bg-transparent p-0 text-accent-ink underline">${notYet.length} not yet indexed · choose which ›</button>` : ''}
    </div>
    <div class="my-s3 h-px bg-rule"></div>
    ${list.length ? `<table class="w-full text-left"><tbody data-fi-list>${rows}</tbody></table>` : ''}`;
  bindSubnav(renderAutomate);
  el.querySelector('[data-fi-choose]')?.addEventListener('click', () =>
    openFirstIndexAsk({ slugs: notYet.map((p) => p.slug), listOpen: true }));
  if (_fiUnsub) _fiUnsub();
  _fiUnsub = subscribeFirstIndex(() => {
    if (_tab !== 'first-index' || !el.isConnected || !el.querySelector('[data-fi-count]')) { _fiUnsub?.(); _fiUnsub = null; return; }
    drawFirstIndexing(state.firstIndex);
  });
}


/* ── Schedule one resource; pause and resume; why a run failed (PI-073, 099, 100, 103) ─────────── */

/** The cadences the scheduler understands (routes/schedules.py `_VALID_SCHEDULES`). */
export const CADENCES = ['manual', 'daily', 'weekly', 'monthly'];

/** On / paused as a control that looks like what it is: a cue (filled or hollow) and a short word. */
export function enabledToggleHtml(s, key) {
  const on = !!s.enabled;
  return `<button type="button" data-sched-toggle="${esc(key)}" data-next-enabled="${on ? '0' : '1'}"
    aria-pressed="${on ? 'true' : 'false'}"
    title="${on ? 'Pause this schedule: it keeps its cadence and stops running' : 'Switch this schedule back on'}"
    class="cursor-pointer rounded-sm border bg-transparent px-2 py-[2px] text-caveat
      ${on ? 'border-rule-strong text-state-ok' : 'border-state-warn text-state-warn'}"
    >${on ? '● on' : '○ paused'}</button>`;
}

function scheduleFormHtml() {
  const slug = state.selectedSlug;
  if (!slug) {
    return `<p data-sched-form-empty class="text-caveat text-ink-muted">Pick a resource in the sidebar to put
      an analysis or a survey of it on a schedule.</p>`;
  }
  return `<form data-sched-form class="flex flex-wrap items-end gap-s2 rounded-sm border border-rule p-s2">
    <div class="w-full text-caps uppercase tracking-caps text-ink-muted">Add a schedule for
      <span class="font-mono normal-case">${esc(slug)}</span></div>
    <label class="text-caveat text-ink-muted">What
      <select data-sched-kind class="ml-1 rounded-sm border border-rule bg-paper px-2 py-[2px] text-caveat text-ink">
        <option value="analysis">an analysis</option><option value="survey">a survey</option></select></label>
    <label class="text-caveat text-ink-muted">Which
      <select data-sched-target class="ml-1 max-w-[28ch] rounded-sm border border-rule bg-paper px-2 py-[2px] text-caveat text-ink">
        <option value="">reading…</option></select></label>
    <label class="text-caveat text-ink-muted">How often
      <select data-sched-cadence class="ml-1 rounded-sm border border-rule bg-paper px-2 py-[2px] text-caveat text-ink">
        ${CADENCES.map((c) => `<option value="${c}" ${c === 'weekly' ? 'selected' : ''}>${c}</option>`).join('')}</select></label>
    <button type="submit" data-sched-save
      class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-caveat text-accent-ink">Schedule</button>
    <span data-sched-form-state class="text-caveat text-ink-muted"></span>
  </form>`;
}

/** The options for one kind of target on one resource kind. Analyses come from the catalog for the resource
 *  kind; surveys from the authored Survey Definitions that suit it. Pure, so a test can pin it. */
export function targetOptions(kind, entityType, { analyses = [], definitions = [] } = {}) {
  if (kind === 'survey') {
    return definitions
      .filter((d) => !d.resource_type || d.resource_type === entityType)
      .map((d) => ({ value: d.qualified_name, label: d.name }));
  }
  return analyses
    .map((a) => ({ value: a.id || a.analysis_id, label: a.name || a.id || a.analysis_id }))
    .filter((o) => o.value);
}

function bindScheduleForm(el, rerender) {
  const form = el.querySelector('[data-sched-form]');
  if (!form) return;
  const entityType = apiEntityType(state.resourceType);
  const slug = state.selectedSlug;
  const kindSel = form.querySelector('[data-sched-kind]');
  const targetSel = form.querySelector('[data-sched-target]');
  const stateEl = form.querySelector('[data-sched-form-state]');
  const data = { analyses: [], definitions: [], failed: '' };

  const fill = () => {
    const opts = targetOptions(kindSel.value, entityType, data);
    targetSel.innerHTML = opts.length
      ? opts.map((o) => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join('')
      : `<option value="">${data.failed ? 'could not read' : 'none available'}</option>`;
  };
  Promise.all([
    listAnalyses(entityType).catch((e) => { data.failed = e.message; return []; }),
    listSurveyDefinitions().catch((e) => { data.failed = e.message; return []; }),
  ]).then(([analyses, definitions]) => { data.analyses = analyses; data.definitions = definitions; fill(); });
  kindSel.addEventListener('change', fill);

  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const target = targetSel.value;
    if (!target) { stateEl.textContent = 'Pick what to schedule first.'; return; }
    const save = form.querySelector('[data-sched-save]');
    save.disabled = true; save.textContent = 'Saving…';
    try {
      await saveSchedule(entityType, slug, target, form.querySelector('[data-sched-cadence]').value, true, kindSel.value);
      rerender();
    } catch (err) {
      save.disabled = false; save.textContent = 'Schedule';
      stateEl.textContent = `Not saved: ${err.message}`;
    }
  });
}

function bindScheduleStateAndErrors(el, schedules, rerender) {
  const byKey = new Map(schedules.map((s) => [`${s.entity_type}|${s.entity_slug}|${s.analysis_id}`, s]));
  el.querySelectorAll('[data-sched-toggle]').forEach((b) => b.addEventListener('click', async () => {
    const s = byKey.get(b.dataset.schedToggle);
    if (!s || b.disabled) return;
    const next = b.dataset.nextEnabled === '1';
    b.disabled = true;
    b.textContent = next ? 'Switching on…' : 'Pausing…';
    try {
      await saveSchedule(s.entity_type, s.entity_slug, s.analysis_id, s.schedule, next, s.target_kind || 'analysis');
      rerender();
    } catch (err) {
      b.disabled = false;
      b.textContent = `Not saved: ${err.message}`;
    }
  }));
  el.querySelectorAll('[data-sched-error]').forEach((b) => b.addEventListener('click', async () => {
    const key = b.dataset.schedError;
    const row = [...el.querySelectorAll('[data-sched-error-row]')].find((r) => r.dataset.schedErrorRow === key);
    if (!row) return;
    const open = row.classList.contains('hidden');
    row.classList.toggle('hidden', !open);
    b.setAttribute('aria-expanded', String(open));
    if (!open || row.dataset.loaded) return;
    const cell = row.firstElementChild;
    const id = b.dataset.activityId;
    if (!id) {
      cell.innerHTML = `<p data-sched-error-detail class="text-caveat text-ink-muted">The run failed, but no
        activity entry was recorded for it, so there is no reason to show.</p>`;
      row.dataset.loaded = '1';
      return;
    }
    cell.innerHTML = '<p class="text-caveat text-ink-muted">reading…</p>';
    try {
      const entry = await getActivityEntry(id);
      cell.innerHTML = `<div data-sched-error-detail class="rounded-sm border border-rule p-s2">
        <div class="text-answer text-ink">${esc(entry.summary || 'The run failed.')}</div>
        ${entry.detail ? readableDetailHtml(entry.detail) : '<p class="text-caveat text-ink-muted">No further detail was recorded.</p>'}</div>`;
      row.dataset.loaded = '1';
    } catch (err) {
      cell.innerHTML = `<p data-sched-error-detail class="text-caveat text-state-warn">Could not read the activity entry: ${esc(err.message)}</p>`;
    }
  }));
}
