/* Admin → Egeria Links (PI-130): resources whose stored Egeria GUID no longer resolves.
 *
 * Port of classic's loadAdminEgeriaLinksPanel against the existing linkage routes
 * (routes/egeria.py `/linkage/stale`, `/linkage/{type}/{slug}/resolve`, `/linkage/resolve-all`).
 *
 * WHAT IS OFFERED, and what deliberately is not. Every resolution clears the unusable link first and differs in
 * what runs next:
 *   Republish  - re-publish what RE already holds (writes a new SurveyReport to Egeria; fast, no re-scan)
 *   Re-survey  - survey from scratch, then publish (writes to Egeria; minutes)
 *   Discard link - clear the link and stop; nothing is written to Egeria
 * Classic's bulk toolbar also had "Delete locally" and "Delete in Egeria". Those are not here: this pane
 * reconciles, it does not remove, and a delete is a person's own act (the owner performs destructive deletes).
 *
 * BULK acts on the rows the person SAW and ticked, never a server-side "everything stale", and previews first
 * (dry run, which the route defaults to) before the real call is offered. */
import {
  listStaleLinkages, resolveStaleLinkage, resolveAllLinkages, getAdminStatus, adminLocked,
} from '/static/re-api.js';
import { esc } from '/static/next/app.js';
import { ago } from '/static/next/format.js';
import { LINK_ACTIONS } from '/static/next/link-actions.js';
import { inPageConfirm, inPageNotice } from '/static/next/confirm-dialog.js';

/** What each choice does, said before it is pressed. Exported so a test can pin that none of them names a delete. */
export const ACTIONS = LINK_ACTIONS;
const ACTION = Object.fromEntries(ACTIONS.map((a) => [a.id, a]));

let _host = null;
let _rows = [];
let _busy = false;
/** true when this caller may not bulk-resolve (see `adminLocked`). Bulk resolve is admin-gated; one resource stays open. */
let _locked = false;
let _preview = null;   // {action, targets, result}

const keyOf = (r) => `${r.entity_type}|${r.entity_slug}`;

function rowHtml(r, i) {
  const key = keyOf(r);
  return `<tr class="border-b border-rule align-top" data-link-row="${esc(key)}">
    <td class="py-s2 pr-s2"><input type="checkbox" data-link-pick="${i}" aria-label="Select ${esc(r.entity_slug)}"></td>
    <td class="py-s2 pr-s3 text-caveat text-ink">${esc(r.entity_type)}/${esc(r.entity_slug)}</td>
    <td class="py-s2 pr-s3 font-mono text-provenance text-ink-muted">${r.stale_guid ? esc(r.stale_guid) : '<span title="No GUID was recorded">not recorded</span>'}</td>
    <td class="py-s2 pr-s3 text-caveat text-ink-muted" title="${esc(r.detected_at || '')}">${r.detected_at ? esc(ago(r.detected_at)) : 'not recorded'}</td>
    <td class="py-s2"><div class="flex flex-wrap gap-[6px]">${ACTIONS.map((a) => `
      <button type="button" data-link-resolve="${a.id}" data-link-key="${esc(key)}" title="${esc(a.does)}"
        class="cursor-pointer rounded-sm border bg-transparent px-2 py-[2px] text-caveat
          ${a.writes ? 'border-rule-strong text-ink hover:border-accent' : 'border-rule text-ink-muted hover:text-ink'}">${esc(a.label)}</button>`).join('')}
    </div><div data-link-state="${esc(key)}" class="mt-[3px] text-provenance text-ink-muted"></div></td>
  </tr>`;
}

function picked() {
  return _rows.filter((_, i) => _host.querySelector(`[data-link-pick="${i}"]`)?.checked);
}

function toolbarHtml() {
  const locked = _locked;
  return `<div data-link-toolbar class="mb-s3 flex flex-wrap items-center gap-s2 rounded-sm border border-rule p-s2 text-caveat">
    ${locked ? '<span data-admin-only class="text-state-warn" title="Bulk resolve writes to Egeria. It needs the admin credential (Admin → Feedback)">admin only</span>' : ''}
    <span data-link-count class="text-ink-muted">0 selected</span>
    <select data-link-bulk-action ${_locked ? 'disabled' : ''} class="rounded-sm border border-rule bg-paper px-2 py-[2px] text-caveat text-ink">
      ${ACTIONS.map((a) => `<option value="${a.id}">${esc(a.label)}</option>`).join('')}</select>
    <button type="button" data-link-preview disabled
      class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink disabled:cursor-default disabled:opacity-50">Preview</button>
    <button type="button" data-link-apply disabled
      class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-caveat text-accent-ink disabled:cursor-default disabled:opacity-50">Apply</button>
    <span data-link-bulk-state class="text-ink-muted"></span>
  </div>`;
}

function render() {
  if (!_host) return;
  _preview = null;
  _host.innerHTML = `
    <h3 class="m-0 font-heading text-name font-normal text-ink">🔗 Egeria Links</h3>
    <p class="mb-s3 mt-s1 max-w-[70ch] text-caveat text-ink-muted">Resources whose stored Egeria GUID is no longer known
      to the repository, normally because Egeria was reset on its own. RE keeps its survey results and stops rather
      than guessing, because re-creating the element automatically could duplicate Egeria records.
      <strong class="text-ink">Every choice clears the unusable link first;</strong> they differ in what runs next.</p>
    ${_rows.length ? toolbarHtml() : ''}
    <div data-link-result></div>
    ${_rows.length ? `<table class="w-full text-left">
      <thead><tr class="border-b border-rule text-caps uppercase tracking-caps text-ink-muted">
        <th class="w-8 pb-s2 pr-s2 font-normal"><input type="checkbox" data-link-all aria-label="Select all"></th>
        <th class="pb-s2 pr-s3 font-normal">Resource</th><th class="pb-s2 pr-s3 font-normal">Stale GUID</th>
        <th class="pb-s2 pr-s3 font-normal">Detected</th><th class="pb-s2 font-normal">Resolve</th>
      </tr></thead><tbody>${_rows.map(rowHtml).join('')}</tbody></table>`
    : `<p data-link-none class="text-answer text-state-ok">✓ No stale links. Every resource RE has published still
        resolves in Egeria, as of the last time something tried to use it.</p>`}`;
  bind();
}

function refreshToolbar() {
  const n = picked().length;
  const count = _host.querySelector('[data-link-count]');
  if (count) count.textContent = `${n} selected`;
  const locked = _locked;
  _host.querySelector('[data-link-preview]')?.toggleAttribute('disabled', locked || n === 0 || _busy);
  // Apply is only ever armed by a preview of exactly this selection and action.
  const apply = _host.querySelector('[data-link-apply]');
  if (apply) apply.toggleAttribute('disabled', locked || !_preview || _busy);
}

function invalidatePreview() {
  _preview = null;
  const st = _host.querySelector('[data-link-bulk-state]');
  if (st) st.textContent = '';
  refreshToolbar();
}

export function outcomeLines(result) {
  return (result?.details || []).map((d) => `${d.entity_type}/${d.slug}: ${d.result}${d.message ? ` — ${d.message}` : ''}`);
}

function showBulkResult(result, { dry }) {
  const out = _host.querySelector('[data-link-result]');
  const bad = (result.failed || 0) > 0;
  out.innerHTML = `<div data-link-bulk-result class="mb-s3 rounded-sm border ${bad ? 'border-state-warn' : 'border-rule'} bg-paper-surface p-s3 text-caveat">
    <div class="${bad ? 'text-state-warn' : 'text-ink'}">${dry ? 'Preview, nothing was changed: ' : ''}<span class="tnum">${result.succeeded || 0}</span> ok ·
      <span class="tnum">${result.failed || 0}</span> failed · <span class="tnum">${result.skipped || 0}</span> skipped</div>
    ${outcomeLines(result).map((l) => `<div class="font-mono text-provenance text-ink-muted">${esc(l)}</div>`).join('')}
  </div>`;
}

async function resolveOne(btn) {
  if (_busy) return;
  const action = btn.dataset.linkResolve;
  const row = _rows.find((r) => keyOf(r) === btn.dataset.linkKey);
  if (!row) return;
  const a = ACTION[action];
  if (!await inPageConfirm({ title: `${a.label} ${row.entity_type}/${row.entity_slug}?`, message: a.does, confirmLabel: a.label })) return;
  _busy = true;
  const st = [..._host.querySelectorAll('[data-link-state]')].find((e) => e.dataset.linkState === btn.dataset.linkKey);
  const original = btn.textContent;
  btn.disabled = true; btn.textContent = 'Working…';
  try {
    const res = await resolveStaleLinkage(row.entity_type, row.entity_slug, action);
    _busy = false;
    _rows = _rows.filter((r) => keyOf(r) !== btn.dataset.linkKey);
    render();
    // The row has left the list; its outcome stays on screen above it.
    _host.querySelector('[data-link-result]').insertAdjacentHTML('beforeend',
      `<p data-link-done class="mb-s2 text-caveat text-state-ok">✓ ${esc(row.entity_type)}/${esc(row.entity_slug)}: ${esc(res.next_step || 'link resolved')}</p>`);
  } catch (err) {
    _busy = false;
    btn.disabled = false; btn.textContent = original;
    if (st) st.innerHTML = `<span class="text-state-warn">Not resolved: ${esc(err.message)}</span>`;
    inPageNotice({ title: `${a.label} did not finish`, message: `${row.entity_type}/${row.entity_slug}: ${err.message}` });
  }
}

async function previewBulk() {
  const targets = picked().map((r) => ({ entity_type: r.entity_type, slug: r.entity_slug }));
  if (!targets.length || _busy) return;
  const action = _host.querySelector('[data-link-bulk-action]').value;
  const st = _host.querySelector('[data-link-bulk-state]');
  _busy = true; refreshToolbar(); st.textContent = 'Previewing…';
  try {
    const result = await resolveAllLinkages(targets, action, { dryRun: true });
    showBulkResult(result, { dry: true });
    _preview = { action, targets };
    st.textContent = `Apply will ${ACTION[action].label.toLowerCase()} these ${targets.length}.`;
  } catch (err) {
    _preview = null;
    st.textContent = `Preview failed: ${err.message}`;
  } finally { _busy = false; refreshToolbar(); }
}

async function applyBulk() {
  if (!_preview || _busy) return;
  const { action, targets } = _preview;
  const a = ACTION[action];
  if (!await inPageConfirm({ title: `${a.label} ${targets.length} resource(s)?`, message: `${a.does}\n\n${targets.map((t) => `${t.entity_type}/${t.slug}`).join('\n')}`, confirmLabel: a.label })) return;
  const st = _host.querySelector('[data-link-bulk-state]');
  _busy = true; refreshToolbar(); st.textContent = 'Applying…';
  try {
    const result = await resolveAllLinkages(targets, action, { dryRun: false });
    _busy = false;
    const done = new Set((result.details || []).filter((d) => d.result === 'ok').map((d) => `${d.entity_type}|${d.slug}`));
    _rows = _rows.filter((r) => !done.has(keyOf(r)));
    render();
    showBulkResult(result, { dry: false });
  } catch (err) {
    _busy = false;
    st.textContent = `Not applied: ${err.message}`;
    inPageNotice({ title: `${a.label} did not finish`, message: err.message });
    refreshToolbar();
  }
}

function bind() {
  _host.querySelectorAll('[data-link-pick]').forEach((c) => c.addEventListener('change', () => { invalidatePreview(); }));
  _host.querySelector('[data-link-all]')?.addEventListener('change', (e) => {
    _host.querySelectorAll('[data-link-pick]').forEach((c) => { c.checked = e.target.checked; });
    invalidatePreview();
  });
  _host.querySelector('[data-link-bulk-action]')?.addEventListener('change', invalidatePreview);
  _host.querySelector('[data-link-preview]')?.addEventListener('click', previewBulk);
  _host.querySelector('[data-link-apply]')?.addEventListener('click', applyBulk);
  _host.querySelectorAll('[data-link-resolve]').forEach((b) => b.addEventListener('click', () => resolveOne(b)));
  if (_rows.length) refreshToolbar();
}

export async function renderEgeriaLinks(host) {
  _host = host;
  _busy = false;
  _preview = null;
  _locked = false;
  host.innerHTML = '<p class="text-answer text-ink-muted">reading…</p>';
  _locked = await getAdminStatus().then(adminLocked, () => false);
  _rows = await listStaleLinkages();
  render();
}
