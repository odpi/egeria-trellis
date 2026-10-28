/* The RFA drawer — /next's own view of RequestForAction.
 *
 * RFA is NOT one of the eight intents (see packages/resource-explorer's own
 * CLAUDE.md) — it is chrome-level infrastructure, a persistent panel
 * reachable from the header regardless of which stage is active, the same
 * role `#rfa-drawer` plays in the classic UI (index.html). This module is a
 * TOP-LEVEL shared module for exactly that reason: like worklist.js, it is
 * imported BY app.js rather than being one of app.js's per-stage panes, so
 * it deliberately does not import `state`/`esc`/`$` back from app.js (that
 * would be circular) and instead carries its own tiny versions of them.
 *
 * SCOPE (PLAN-FINISH-REPOS.md item 10, "RFAs — not started"): let someone
 * see and act on their RFAs without leaving /next. That means:
 *   - list open RFAs, globally and filtered to the resource in view;
 *   - show what each one is about (entity, analysis, summary, explanation,
 *     action requested/target — the fields the classic drawer already
 *     reads off the flattened `/api/activity/rfas` row, `activity.py`'s
 *     `_emit_rfa`);
 *   - take the three response actions that already exist server-side (recorded locally,
 *     then synced to Egeria best-effort):
 *     defer / reassign / complete (plus reopen, which is the same PATCH
 *     with `status: 'open'` — `RFA_STATUSES` in `web/routes/activity.py`).
 *
 * ALSO (parity PI-112/113/114): dismiss a request as not applicable / won't do
 * with an optional note (`docs/rfa-dismissals.md`), restore a dismissal, and a
 * free-text note on any request. A dismissal is a RECORD: the row stays in the
 * list behind "show suppressed (N)" with its reason, who and when, and a restore
 * keeps the record, marked cleared. Dismiss and restore write only to Resource
 * Explorer's registry. A status change or a note is different: the local write is
 * authoritative, and `rfa_egeria_sync.py` then makes a best-effort Egeria write
 * server-side (a ToDo for a status change; an ActivityEntry note on that ToDo for a
 * note, only once the ToDo exists). A failed sync never fails the local write.
 * The note form says so when the request already has a ToDo (`egeria_todo_guid`).
 *
 * NOT in scope here, deliberately: a real actor-GUID story for the ToDo
 * (`resource_explorer/registry.py`'s `rfa_actions` table docstring and
 * `docs/egeria-integration.md` §11); this drawer's job ends at the local write
 * succeeding.
 */

import { listRfas, updateRfaAction, dismissRfa, restoreRfaDismissal, saveRfaNote } from '/static/re-api.js';
import { ago } from '/static/next/format.js';
import { stateEntry } from '/static/next/glyphs.js';
import { cue } from '/static/next/stages/repo-manifest.js';
import { inPagePrompt } from '/static/next/confirm-dialog.js';

function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

const STATUS_LABEL = {
  open: 'Open',
  deferred: 'Deferred',
  reassigned: 'Reassigned',
  completed: 'Completed',
};

const STATUS_TONE = {
  open: 'text-accent-ink',
  deferred: 'text-state-warn',
  reassigned: 'text-state-warn',
  completed: 'text-state-ok',
};

let _drawerEl = null;
let _rfas = [];          // last fetch, raw rows from GET /api/activity/rfas
let _loadTicket = 0;     // guards against a slow fetch landing after a newer one started
let _scopeSlug = '';     // '' = all resources; set from the caller at open time
let _scopeOnly = true;   // "this resource" vs "all" — only meaningful when _scopeSlug is set
let _showClosed = false; // include completed alongside open/deferred/reassigned
let _showSuppressed = false; // include dismissed requests (always counted, never silently dropped)

const DISMISS_REASONS = [['not_applicable', 'Not applicable'], ['wont_do', "Won't do"]];
const reasonLabel = (r) => (DISMISS_REASONS.find(([k]) => k === r) || [r, r])[1];

function _buildShell() {
  const el = document.createElement('aside');
  el.id = 'next-rfa-drawer';
  el.className = 'fixed inset-y-0 right-0 z-[80] flex w-[26rem] max-w-[92vw] flex-col '
    + 'border-l border-chrome-line bg-chrome text-chrome-ink shadow-lg';
  el.style.display = 'none';
  el.innerHTML = `
    <div class="flex items-center justify-between border-b border-chrome-line px-s3 py-s2">
      <span class="font-heading text-caps uppercase tracking-caps text-chrome-ink">Open RFAs</span>
      <button type="button" id="next-rfa-close" aria-label="Close"
        class="cursor-pointer bg-transparent text-chrome-muted hover:text-chrome-ink">×</button>
    </div>
    <div class="flex items-center gap-s3 border-b border-chrome-line px-s3 py-s2 text-chip text-chrome-muted">
      <label class="flex cursor-pointer items-center gap-[6px]">
        <input type="checkbox" id="next-rfa-scope-only">
        <span id="next-rfa-scope-label">this resource only</span>
      </label>
      <label class="flex cursor-pointer items-center gap-[6px]">
        <input type="checkbox" id="next-rfa-show-closed">
        <span>show completed</span>
      </label>
      <label class="flex cursor-pointer items-center gap-[6px]">
        <input type="checkbox" id="next-rfa-show-suppressed">
        <span id="next-rfa-suppressed-label">show suppressed</span>
      </label>
    </div>
    <div id="next-rfa-flash" class="px-s3 text-provenance text-chrome-muted" aria-live="polite"></div>
    <div id="next-rfa-list" class="flex-1 overflow-y-auto px-s3 py-s2 text-resource">reading…</div>
  `;
  document.body.appendChild(el);

  el.querySelector('#next-rfa-close').addEventListener('click', closeRfaDrawer);
  el.querySelector('#next-rfa-scope-only').addEventListener('change', (e) => {
    _scopeOnly = e.target.checked;
    _renderList();
  });
  el.querySelector('#next-rfa-show-closed').addEventListener('change', (e) => {
    _showClosed = e.target.checked;
    _renderList();
  });
  el.querySelector('#next-rfa-show-suppressed').addEventListener('change', (e) => {
    _showSuppressed = e.target.checked;
    _renderList();
  });
  el.querySelector('#next-rfa-list').addEventListener('click', _onListClick);

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && el.style.display !== 'none') closeRfaDrawer();
  });

  return el;
}

/** Rows in scope (this resource, or all) before the closed/suppressed filters, so the counts are honest. */
function _scopedRows() {
  return (_scopeSlug && _scopeOnly) ? _rfas.filter((r) => r.entity_slug === _scopeSlug) : _rfas;
}

function _visibleRows() {
  let rows = _scopedRows();
  if (!_showClosed) rows = rows.filter((r) => r.rfa_status !== 'completed');
  if (!_showSuppressed) rows = rows.filter((r) => !r.dismissed);
  // Newest first — matches the classic drawer and the activity log's own convention.
  return [...rows].sort((a, b) => String(b.ts || '').localeCompare(String(a.ts || '')));
}

function _rowHtml(rfa) {
  // The corner shows the request's real state: a dismissed one reads "dismissed" with its reason
  // word, never the underlying status ("Open") it had before it was dismissed.
  const tone = rfa.dismissed ? 'text-chrome-muted' : (STATUS_TONE[rfa.rfa_status] || 'text-chrome-muted');
  const label = rfa.dismissed
    ? `dismissed · ${reasonLabel((rfa.dismissal || {}).reason)}`
    : (STATUS_LABEL[rfa.rfa_status] || rfa.rfa_status);
  const who = rfa.entity_name || rfa.entity_slug || 'unknown resource';
  const what = rfa.analysis_name || rfa.annotation_type || 'RFA';
  const d = rfa.dismissal || {};
  const dismissedNote = rfa.dismissed
    ? `<div data-rfa-dismissed class="mt-[2px] text-provenance text-chrome-muted"><span class="font-glyph" aria-hidden="true">${stateEntry('unrun').glyph}</span>
        dismissed · ${esc(reasonLabel(d.reason))}${d.note ? ` · ${esc(d.note)}` : ''}${
          d.created_by ? ` · ${esc(d.created_by)}` : ''}${d.created_at ? ` · ${esc(ago(d.created_at))}` : ''}</div>`
    : '';
  const noteLine = rfa.notes
    ? `<div data-rfa-note class="mt-[2px] whitespace-pre-wrap text-provenance text-chrome-ink">note: ${esc(rfa.notes)}</div>` : '';
  const assigneeNote = rfa.assignee
    ? ` · assigned to <span class="text-chrome-ink">${esc(rfa.assignee)}</span>`
    : '';
  const deferNote = rfa.rfa_status === 'deferred' && rfa.defer_until
    ? ` · until <span class="tnum">${esc(rfa.defer_until)}</span>`
    : '';
  const canAct = rfa.rfa_status !== 'completed' && !rfa.dismissed;
  const actions = `
    ${canAct ? `<button type="button" data-rfa-act="deferred" data-rfa-id="${esc(rfa.id)}"
        class="cursor-pointer bg-transparent text-accent-ink underline">Defer</button>` : ''}
    ${canAct ? `<button type="button" data-rfa-act="reassigned" data-rfa-id="${esc(rfa.id)}"
        class="cursor-pointer bg-transparent text-accent-ink underline">Reassign</button>` : ''}
    ${canAct ? `<button type="button" data-rfa-act="completed" data-rfa-id="${esc(rfa.id)}"
        class="cursor-pointer bg-transparent text-accent-ink underline">Complete</button>` : ''}
    ${rfa.rfa_status !== 'open' && !rfa.dismissed ? `<button type="button" data-rfa-act="open" data-rfa-id="${esc(rfa.id)}"
        class="cursor-pointer bg-transparent text-chrome-muted underline">Reopen</button>` : ''}
    ${rfa.dismissed
      ? `<button type="button" data-rfa-restore="${esc(d.id || '')}" data-rfa-id="${esc(rfa.id)}"
          class="cursor-pointer bg-transparent text-accent-ink underline">Restore</button>`
      : `<button type="button" data-rfa-dismiss="${esc(rfa.id)}"
          class="cursor-pointer bg-transparent text-chrome-muted underline">Dismiss…</button>`}
    <button type="button" data-rfa-note-open="${esc(rfa.id)}"
      class="cursor-pointer bg-transparent text-chrome-muted underline">${rfa.notes ? 'Edit note' : 'Add note'}</button>
  `;
  return `
    <div class="border-b border-chrome-line py-s2" data-rfa-row="${esc(rfa.id)}">
      <div class="flex items-start justify-between gap-s2">
        <div class="min-w-0">
          <div class="truncate text-provenance text-chrome-muted">${esc(who)} · ${esc(what)}</div>
          <div class="text-resource text-chrome-ink">${esc(rfa.summary || '(no summary)')}</div>
          ${rfa.explanation ? `<div class="mt-[2px] text-provenance text-chrome-muted">${esc(rfa.explanation)}</div>` : ''}
          ${rfa.action_requested ? `<div class="mt-[2px] text-provenance text-chrome-muted">requested: ${esc(rfa.action_requested)}${
            rfa.action_target_name ? ` — ${esc(rfa.action_target_name)}` : ''}</div>` : ''}
        </div>
        <span data-rfa-corner class="shrink-0 whitespace-nowrap text-provenance ${tone}">${rfa.dismissed ? `<span class="font-glyph" aria-hidden="true">${stateEntry('unrun').glyph}</span> ` : ''}${esc(label)}</span>
      </div>
      <div class="mt-[2px] text-provenance text-chrome-muted">${assigneeNote}${deferNote}</div>
      ${dismissedNote}${noteLine}
      <div class="mt-s2 flex flex-wrap gap-s3 text-chip" data-rfa-actions>${actions}</div>
      <div data-rfa-form class="mt-s2"></div>
    </div>
  `;
}

function _renderList() {
  if (!_drawerEl) return;
  const host = _drawerEl.querySelector('#next-rfa-list');
  const scopeLabel = _drawerEl.querySelector('#next-rfa-scope-label');
  const scopeOnlyBox = _drawerEl.querySelector('#next-rfa-scope-only');
  if (scopeLabel) scopeLabel.textContent = _scopeSlug ? `this resource only (${_scopeSlug})` : 'this resource only';
  if (scopeOnlyBox) scopeOnlyBox.disabled = !_scopeSlug;

  const suppressed = _scopedRows().filter((r) => r.dismissed).length;
  const supLabel = _drawerEl.querySelector('#next-rfa-suppressed-label');
  if (supLabel) supLabel.textContent = `show suppressed (${suppressed})`;

  const rows = _visibleRows();
  if (!rows.length) {
    host.innerHTML = `<p class="text-caveat text-chrome-muted">
      No ${_showClosed ? '' : 'open '}RFAs${_scopeSlug && _scopeOnly ? ` for ${esc(_scopeSlug)}` : ''}.
    </p>`;
    return;
  }
  host.innerHTML = rows.map(_rowHtml).join('');
}

const rowEl = (rfaId) => [..._drawerEl.querySelectorAll('[data-rfa-row]')].find((n) => n.dataset.rfaRow === rfaId);
const flash = (msg) => { const f = _drawerEl && _drawerEl.querySelector('#next-rfa-flash'); if (f) f.textContent = msg; };
const formBtn = (attr, label) => `<button type="button" ${attr} class="cursor-pointer rounded-sm border border-chrome-line bg-transparent px-2 py-[1px] text-chip text-chrome-ink disabled:cursor-default disabled:opacity-60">${label}</button>`;

/** The dismiss form: a reason (required) and an optional note, inline under the row. */
function _openDismissForm(rfaId) {
  const slot = rowEl(rfaId)?.querySelector('[data-rfa-form]');
  if (!slot) return;
  slot.innerHTML = `<div data-rfa-dismiss-form class="flex flex-wrap items-center gap-s2">
    <select data-rfa-reason class="rounded-sm border border-chrome-line bg-transparent px-[6px] py-[2px] text-provenance text-chrome-ink placeholder:text-chrome-muted"><option value="">Why?</option>${
      DISMISS_REASONS.map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join('')}</select>
    <input data-rfa-dismiss-note type="text" placeholder="note (optional)" class="min-w-0 flex-1 rounded-sm border border-chrome-line bg-transparent px-[6px] py-[2px] text-provenance text-chrome-ink placeholder:text-chrome-muted">
    ${formBtn('data-rfa-dismiss-go disabled', 'Dismiss')}${formBtn('data-rfa-form-cancel', 'Cancel')}
    <span data-rfa-form-status class="text-provenance text-chrome-muted"></span></div>`;
  const reason = slot.querySelector('[data-rfa-reason]');
  const go = slot.querySelector('[data-rfa-dismiss-go]');
  reason.addEventListener('change', () => { go.disabled = !reason.value; });
  slot.querySelector('[data-rfa-form-cancel]').addEventListener('click', () => { slot.innerHTML = ''; });
  go.addEventListener('click', async () => {
    if (go.disabled) return;
    go.disabled = true;
    const status = slot.querySelector('[data-rfa-form-status]');
    status.textContent = 'saving…';
    try {
      const out = await dismissRfa(rfaId, { reason: reason.value, note: slot.querySelector('[data-rfa-dismiss-note]').value.trim() });
      const key = (_rfas.find((r) => r.id === rfaId) || {}).dismissal_key;
      let hidden = 0;
      _rfas.forEach((r) => { if (r.dismissal_key === key) { r.dismissed = true; r.dismissal = out.dismissal; hidden += 1; } });
      flash(`dismissed · ${reasonLabel(out.dismissal && out.dismissal.reason)}${_showSuppressed ? '' : ` · ${hidden} hidden, shown under "show suppressed"`}`);
      _renderList();
    } catch (err) {
      go.disabled = false;
      status.textContent = `not recorded: ${err && err.message ? err.message : 'could not reach the server'}`;
    }
  });
}

/** Restore: the dismissal row is kept (marked cleared), the request returns to the list. */
async function _restore(btn) {
  const rfaId = btn.dataset.rfaId;
  const did = btn.dataset.rfaRestore;
  if (!did || btn.disabled) return;
  btn.disabled = true;
  btn.textContent = 'restoring…';
  try {
    await restoreRfaDismissal(did);
    _rfas.forEach((r) => { if (r.dismissal && r.dismissal.id === did) { r.dismissed = false; r.dismissal = null; } });
    flash('restored · the dismissal is kept as history');
    _renderList();
  } catch (err) {
    btn.disabled = false;
    btn.textContent = 'Restore';
    const slot = rowEl(rfaId)?.querySelector('[data-rfa-form]');
    if (slot) slot.innerHTML = `<span class="text-state-warn">not restored: ${esc(err && err.message ? err.message : 'could not reach the server')}</span>`;
  }
}

/** The note form: free text, independent of the status. Saving an empty box clears the note. */
function _openNoteForm(rfaId) {
  const row = _rfas.find((r) => r.id === rfaId);
  const slot = rowEl(rfaId)?.querySelector('[data-rfa-form]');
  if (!row || !slot) return;
  slot.innerHTML = `<div data-rfa-note-form class="flex flex-col gap-s2">
    <textarea data-rfa-note-text rows="2" placeholder="note" class="w-full rounded-sm border border-chrome-line bg-transparent px-[6px] py-[2px] text-provenance text-chrome-ink placeholder:text-chrome-muted">${esc(row.notes || '')}</textarea>
    ${row.egeria_todo_guid ? '<div data-rfa-note-egeria class="text-provenance text-chrome-muted">also sent to Egeria, as a note on this request\'s ToDo</div>' : ''}
    <div class="flex items-center gap-s2">${formBtn('data-rfa-note-save', 'Save note')}${formBtn('data-rfa-form-cancel', 'Cancel')}
      <span data-rfa-form-status class="text-provenance text-chrome-muted"></span></div></div>`;
  slot.querySelector('[data-rfa-form-cancel]').addEventListener('click', () => { slot.innerHTML = ''; });
  const save = slot.querySelector('[data-rfa-note-save]');
  save.addEventListener('click', async () => {
    if (save.disabled) return;
    save.disabled = true;
    const status = slot.querySelector('[data-rfa-form-status]');
    status.textContent = 'saving…';
    const text = slot.querySelector('[data-rfa-note-text]').value.trim();
    try {
      await saveRfaNote(rfaId, text);
      row.notes = text;
      _renderList();
    } catch (err) {
      save.disabled = false;
      status.textContent = `not saved: ${err && err.message ? err.message : 'could not reach the server'}`;
    }
  });
}

async function _onListClick(e) {
  const dismiss = e.target.closest('[data-rfa-dismiss]');
  if (dismiss) { _openDismissForm(dismiss.dataset.rfaDismiss); return; }
  const restore = e.target.closest('[data-rfa-restore]');
  if (restore) { await _restore(restore); return; }
  const noteOpen = e.target.closest('[data-rfa-note-open]');
  if (noteOpen) { _openNoteForm(noteOpen.dataset.rfaNoteOpen); return; }
  const btn = e.target.closest('[data-rfa-act]');
  if (!btn) return;
  const status = btn.dataset.rfaAct;
  const rfaId = btn.dataset.rfaId;
  const row = _rfas.find((r) => r.id === rfaId);
  if (!row) return;

  let assignee = row.assignee || '';
  let deferUntil = row.defer_until || '';
  let resolutionNote = row.resolution_note || '';

  if (status === 'reassigned') {
    const typed = await inPagePrompt({ title: 'Assign', message: 'Assign to (name or email):', label: 'Assign to', defaultValue: assignee, confirmLabel: 'Assign' });
    if (typed === null) return;      // cancelled — nothing recorded
    assignee = typed.trim();
  } else if (status === 'deferred') {
    const typed = await inPagePrompt({ title: 'Defer', message: 'Defer until (any text — a date, "next survey", etc.):', label: 'Defer until', defaultValue: deferUntil, confirmLabel: 'Defer' });
    if (typed === null) return;
    deferUntil = typed.trim();
  } else if (status === 'completed') {
    const typed = await inPagePrompt({ title: 'Complete', message: 'Resolution note (optional):', label: 'Resolution note', defaultValue: resolutionNote, confirmLabel: 'Complete' });
    if (typed === null) return;
    resolutionNote = typed.trim();
  }

  const actionsHost = _drawerEl.querySelector(`[data-rfa-row="${CSS.escape(rfaId)}"] [data-rfa-actions]`);
  if (actionsHost) actionsHost.textContent = 'saving…';

  try {
    await updateRfaAction(rfaId, { status, assignee, deferUntil, resolutionNote });
    // Reflect it locally rather than a full reload — the row's own PATCH
    // response already told us it succeeded, and a full re-fetch would
    // discard the scope/closed toggles' current state for no reason.
    Object.assign(row, {
      rfa_status: status, assignee, defer_until: deferUntil, resolution_note: resolutionNote,
    });
    _renderList();
  } catch (err) {
    if (actionsHost) {
      // Brief Z3: refused by the zone check on its Egeria ToDo (403): nothing was recorded in either place; the
      // cue says so, the reason is on hover.
      actionsHost.innerHTML = err && err.status === 403
        ? `<span data-rfa-not-permitted>${cue('no_access', 'not permitted', err.message)}</span>`
        : `<span class="text-state-warn">not recorded: ${esc(err && err.message ? err.message : 'could not reach the server')}</span>`;
    }
  }
}

async function _load() {
  const ticket = ++_loadTicket;
  const host = _drawerEl.querySelector('#next-rfa-list');
  host.innerHTML = 'reading…';
  try {
    const rows = await listRfas();
    if (ticket !== _loadTicket) return;   // a newer open()/refresh() already landed
    _rfas = Array.isArray(rows) ? rows : [];
    _renderList();
  } catch (err) {
    if (ticket !== _loadTicket) return;
    host.innerHTML = `<p class="text-caveat text-state-warn">
      Could not read RFAs: ${esc(err && err.message ? err.message : 'unknown error')}
    </p>`;
  }
}

/** Open the drawer, scoped to `slug` if given (the resource currently in
 *  view) — the toggle still lets the viewer widen to every RFA. Re-fetches
 *  every open, so a defer/reassign/complete made on the previous visit (or
 *  by another tab) is never shown stale. */
export function openRfaDrawer(slug = '') {
  if (!_drawerEl) _drawerEl = _buildShell();
  _scopeSlug = slug || '';
  flash('');
  const scopeOnlyBox = _drawerEl.querySelector('#next-rfa-scope-only');
  if (scopeOnlyBox) scopeOnlyBox.checked = _scopeOnly && !!_scopeSlug;
  _drawerEl.style.display = 'flex';
  _load();
}

export function closeRfaDrawer() {
  if (_drawerEl) _drawerEl.style.display = 'none';
}

export function isRfaDrawerOpen() {
  return !!_drawerEl && _drawerEl.style.display !== 'none';
}

/** The header button's handler: toggle, scoped to whatever resource is
 *  currently in view (app.js passes `state.slug`). */
export function toggleRfaDrawer(slug = '') {
  if (isRfaDrawerOpen()) {
    closeRfaDrawer();
  } else {
    openRfaDrawer(slug);
  }
}
