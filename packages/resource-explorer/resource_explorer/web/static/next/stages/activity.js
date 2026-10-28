/* Activity — the operation log, readable in /next.
 *
 * PLAN-FINISH-REPOS.md Part 3 item 6: "the activity log is readable in
 * /next, not only a data source." Named as an example file alongside
 * enrichment.js/understanding.js/curate.js, but Activity is NOT one of the
 * nine canonical stage ids in app.js's `STAGES` array (investigation,
 * scouting, discovery, assessment, analysis, enrichment, understanding,
 * curate, automate). Per packages/resource-explorer/CLAUDE.md, the 📋
 * Activity log is a persistent surface reachable from the header — decoupled
 * from `#intent-nav`/`currentNavIntent`, the same pattern as ⚙ Admin — not
 * something a user does to a specific resource under one of the eight
 * intents. So this is a header-triggered overlay panel, not a STAGES entry
 * with `built: true`.
 *
 * SCOPE. The classic UI's activity view (index.html's `renderActivityLog`)
 * merges a client-only in-memory log with `GET /api/activity/` results, has
 * a client-side "Clear" that only clears the in-memory merge, an unread
 * badge, and a `_openActivityEntry` deep-link jump used by one caller. None
 * of that survives a reload on its own — the persistent record is entirely
 * server-side (`activity_log` table, `GET /api/activity/`). This pane reads
 * that same endpoint directly: no client-side merge, no fabricated "Clear"
 * that cannot touch the record it names. What it keeps from classic: one
 * row per operation, an expandable detail (annotations tally, cataloged
 * items, free-text detail), and GUIDs rendered short with click-to-copy.
 * What it deliberately leaves out: RFA rendering (the RFA drawer, `#rfa-
 * drawer`, is /next's OWN separate item — this reads the same `annotations`
 * field but its RequestForAction rows belong to that surface, not this one;
 * this list still SHOWS an rfa-operation row, since rule 16 requires it be
 * logged and hiding it would be exactly the "logged but invisible" gap that
 * rule closes).
 *
 * FILTERS. A text filter (slug or summary) and the status chips, plus the
 * classic advanced filter (PI-122): resource kind, stage (intent), operation
 * and "since", under a "filters" disclosure that says how many are set. Those
 * go to the server (`GET /api/activity/?entity_type=&intent=&operation=&status=&since=`)
 * so they apply before the page limit, not only to the page already loaded.
 * The unread badge and the toast that jumps to an entry are in
 * activity-unread.js; opening the panel marks what it shows as seen.
 */
import { listActivity } from '/static/re-api.js';
import { esc, tnum } from '/static/next/app.js';
import { ago } from '/static/next/format.js';
import { markSeen } from '/static/next/activity-unread.js';
import { publishProofHtml, publishStillQueued } from '/static/next/publish-proof.js';

const PANEL_ID = 'activity-panel';
const FETCH_LIMIT = 300;

const OPERATION_LABEL = {
  survey: 'Survey', catalog: 'Cataloguer → Egeria', publish: 'Publish → Egeria',
  scout: 'Scout', discover: 'Discover', refresh: 'Refresh', rfa: 'RFA',
  analysis_run: 'Analysis run',
};
const OPERATION_ICON = {
  survey: '📊', catalog: '☁', publish: '☁', scout: '🔍', discover: '🔍',
  refresh: '🔄', rfa: '📝', analysis_run: '⚙',
};
const ENTITY_ICON = { repo: '📁', database: '🗄', server: '🖥', filesystem: '📂', file: '📄' };
const STATUS_TONE = {
  ok: 'text-state-ok', success: 'text-state-ok',
  error: 'text-state-warn', failed: 'text-state-warn',
  running: 'text-accent-ink', queued: 'text-accent-ink', pending: 'text-accent-ink',
};
const STATUS_GLYPH = { ok: '✓', success: '✓', error: '✗', failed: '✗', running: '◔', queued: '◔', pending: '⏳' };

const GUID_RX = /([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/gi;

/** Same short-GUID-with-copy convenience classic's activity log offers,
 *  applied AFTER esc() so it operates on already-safe text. */
function hiGuid(escapedText) {
  return escapedText.replace(GUID_RX, (m) =>
    `<span class="cursor-pointer font-mono text-provenance text-ink-muted hover:text-ink"
      title="${m} — click to copy" data-copy-guid="${m}">${m.slice(0, 8)}…</span>`);
}

/** Local, per-open state — not persisted, not shared with app.js's `state`.
 *  Filters reset every time the panel opens, same as the classic tab does
 *  not remember a filter across a reload. */
const panel = {
  entries: null, error: null, statusFilter: 'all', textFilter: '', order: 'desc',
  // The advanced filter (PI-122): sent to the server, so it applies before the page limit.
  adv: { entityType: '', intent: '', operation: '', since: '' }, focusId: '',
};

/** The advanced filter's choices. Operation and entity type reuse the labels the rows already draw. */
export const INTENTS = ['investigation', 'scouting', 'discovery', 'assessment', 'analysis', 'enrichment',
  'understanding', 'curate', 'automate'];
export const SINCE_CHOICES = [
  ['', 'any time'], ['1h', 'last hour'], ['24h', 'last 24 hours'], ['7d', 'last 7 days'], ['30d', 'last 30 days'],
];
const SINCE_MS = { '1h': 3600e3, '24h': 86400e3, '7d': 7 * 86400e3, '30d': 30 * 86400e3 };

/** The `since` query value for a choice: a timestamp in the log's own shape (no zone suffix), which compares
 *  correctly as text. Empty for "any time". */
export function sinceValue(choice, now = Date.now()) {
  const ms = SINCE_MS[choice];
  return ms ? new Date(now - ms).toISOString().slice(0, 19) : '';
}

/** Everything the server is asked to filter on. */
export function serverFilters() {
  return {
    entityType: panel.adv.entityType, intent: panel.adv.intent, operation: panel.adv.operation,
    status: panel.statusFilter === 'all' ? '' : panel.statusFilter, since: sinceValue(panel.adv.since),
  };
}

async function reload() {
  panel.entries = null;
  panel.error = null;
  renderList();
  try {
    panel.entries = await listActivity(FETCH_LIMIT, serverFilters());
  } catch (err) {
    panel.error = err.message;
  }
  if (!document.getElementById(PANEL_ID)) return;
  renderList();
  refreshTicks = 0;
  scheduleRefresh();
}

/** Order by time. 'desc' = latest first (what the page has always shown, and
 *  what the server returns: `ORDER BY ts DESC LIMIT n`). The route has no
 *  order parameter, so 'asc' reorders only what was loaded; renderControls
 *  says so when the load was cut off. Remembered per browser; storage that is
 *  missing or throws just means the default. */
const ORDER_KEY = 're.activity.order';
const ORDER_LABEL = { desc: '↓ latest first', asc: '↑ earliest first' };

export function readOrder() {
  try {
    return globalThis.localStorage?.getItem(ORDER_KEY) === 'asc' ? 'asc' : 'desc';
  } catch { return 'desc'; }
}
function writeOrder(order) {
  try { globalThis.localStorage?.setItem(ORDER_KEY, order); } catch { /* not remembered */ }
}

/** Sort by the entry's `ts` (the field the row shows); ties by id, same
 *  direction; an entry with no readable timestamp goes last either way. */
export function sortByTime(entries, order) {
  const dir = order === 'asc' ? 1 : -1;
  const t = (e) => { const n = Date.parse(e?.ts ?? ''); return Number.isNaN(n) ? null : n; };
  return [...entries].sort((a, b) => {
    const ta = t(a); const tb = t(b);
    if (ta === null && tb === null) return dir * String(a?.id ?? '').localeCompare(String(b?.id ?? ''));
    if (ta === null) return 1;
    if (tb === null) return -1;
    if (ta !== tb) return dir * (ta - tb);
    return dir * String(a?.id ?? '').localeCompare(String(b?.id ?? ''));
  });
}

/** While the open list shows an entry that has not finished, re-read the log on a timer so the row turns finished
 *  (and new entries appear) without a reload. Bounded: it stops the moment nothing is running, when the panel
 *  closes, or after maxTicks. The tests shorten intervalMs. */
// 2 s for the first minute, then 10 s; bounded only by two hours of a row still showing running (the header's
// 30 s watch, noticeActivity, covers what follows).
export const refreshConfig = { intervalMs: 2000, fastForMs: 60000, slowMs: 10000, maxTicks: 800 };
const RUNNING = new Set(['running', 'queued', 'pending']);
let refreshTimer = null;
let refreshTicks = 0;

/** Still going: the operation itself, or its publish (queued until the drain lands and reads it back), so the
 *  open list re-reads until the publish word reaches its proof. */
export function isLive(e) {
  return RUNNING.has(String(e?.status || '').toLowerCase()) || publishStillQueued(e?.publish_proof);
}
export function anyRunning(entries) {
  return (entries || []).some(isLive);
}
/** The key a change is noticed by: the status and the publish state together. */
function liveKey(e) {
  return `${String(e?.status || '').toLowerCase()}|${String(e?.publish_proof?.state || '')}`;
}
function stopRefresh() {
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = null;
}
/** Re-read the open list with its filters and redraw, keeping opened details open. False when the panel closed
 *  meanwhile; a failed read keeps the last list (the next tick tries again). */
async function refreshOnce() {
  try {
    const fresh = await listActivity(FETCH_LIMIT, serverFilters());
    if (!document.getElementById(PANEL_ID)) return false;
    panel.entries = fresh;
    panel.error = null;
    markSeen(document, fresh);
    // Keep the details the reader has opened open across the redraw.
    const open = [...document.querySelectorAll('#activity-panel-body [id^="activity-d-"]')]
      .filter((d) => !d.classList.contains('hidden')).map((d) => d.id);
    renderList();
    open.forEach((id) => document.getElementById(id)?.classList.remove('hidden'));
  } catch { /* keep the last list */ }
  return !!document.getElementById(PANEL_ID);
}
function scheduleRefresh() {
  stopRefresh();
  if (!document.getElementById(PANEL_ID) || !anyRunning(panel.entries)) { refreshTicks = 0; return; }
  if (refreshTicks >= refreshConfig.maxTicks) return;
  refreshTimer = setTimeout(async () => {
    refreshTimer = null;
    refreshTicks += 1;
    if (!(await refreshOnce())) return;
    scheduleRefresh();
  }, refreshTicks * refreshConfig.intervalMs < refreshConfig.fastForMs ? refreshConfig.intervalMs : refreshConfig.slowMs);
}

const noticed = new Set();  // `id:status` pairs already acted on, so a filtered-out entry costs one re-read, not one per tick
/** Backlog 7f.6, the cases the running-row poll cannot see: the list was opened while nothing ran (so it is not
 *  polling) and a scan started afterwards; or the poll's budget ran out while a row still showed running. The
 *  header's activity watch (activity-unread.js, every 30 s) hands each page it reads here. The open, not-polling
 *  list re-reads once when that page has an entry the list lacks, or an entry whose running-ness differs from the
 *  list's (running there and finished here, or finished there and still running here); the bounded running-row
 *  poll then takes over if anything runs. Keyed on id:status, so each change costs one re-read. Does nothing when
 *  the panel is closed or already polling. Returns whether it refreshed. */
export async function noticeActivity(entries) {
  if (!document.getElementById(PANEL_ID) || refreshTimer || !Array.isArray(panel.entries)) return false;
  const shown = new Map(panel.entries.map((e) => [e?.id, e]));
  const fresh = (entries || []).filter((e) => {
    if (!e?.id) return false;
    const before = shown.get(e.id);
    const changed = !shown.has(e.id) || isLive(e) !== isLive(before) || liveKey(e) !== liveKey(before);
    return changed && !noticed.has(`${e.id}:${liveKey(e)}`);
  });
  if (!fresh.length) return false;
  fresh.forEach((e) => noticed.add(`${e.id}:${liveKey(e)}`));
  refreshTicks = 0;
  if (!(await refreshOnce())) return false;
  scheduleRefresh();
  return true;
}

function closeActivityPanel() {
  stopRefresh();
  refreshTicks = 0;
  noticed.clear();
  document.getElementById(PANEL_ID)?.remove();
  document.removeEventListener('keydown', onPanelKeydown);
}

function onPanelKeydown(e) {
  if (e.key === 'Escape') closeActivityPanel();
}

/** Open the panel, freshly fetched every time — activity keeps happening
 *  while the panel is closed, and a stale cached page would misreport a
 *  finished run as still `running`. */
export async function openActivityPanel({ focusId = '' } = {}) {
  closeActivityPanel();
  panel.entries = null;
  panel.error = null;
  panel.statusFilter = 'all';
  panel.textFilter = '';
  panel.adv = { entityType: '', intent: '', operation: '', since: '' };
  panel.focusId = focusId;
  panel.order = readOrder();

  const el = document.createElement('div');
  el.id = PANEL_ID;
  el.className = 'fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-s4 overflow-auto';
  el.innerHTML = `
    <div class="mt-[4vh] w-full max-w-[760px] rounded bg-paper p-s4 shadow-lg"
      role="dialog" aria-modal="true" aria-label="Activity log">
      <div class="flex items-start justify-between gap-s3">
        <div>
          <div class="font-heading text-name text-ink">Activity</div>
          <div class="mt-[2px] text-caveat text-ink-muted">Every scouting, survey,
            publish and RFA operation this instance has recorded, read from the
            same log the current UI's Activity tab shows.</div>
        </div>
        <button type="button" data-act="close" class="text-ink-muted hover:text-ink" aria-label="Close">×</button>
      </div>
      <div class="my-s3 h-px bg-rule"></div>
      <div id="activity-panel-controls" class="flex flex-wrap items-center gap-s3 text-caveat"></div>
      <div id="activity-panel-body" class="mt-s3 max-h-[62vh] overflow-y-auto text-caveat text-ink-muted">reading…</div>
    </div>`;
  document.body.appendChild(el);
  el.addEventListener('click', (e) => {
    if (e.target === el || e.target.closest('[data-act="close"]')) closeActivityPanel();
    const copy = e.target.closest('[data-copy-guid]');
    if (copy) navigator.clipboard?.writeText(copy.dataset.copyGuid).catch(() => {});
    const toggle = e.target.closest('[data-activity-toggle]');
    if (toggle) {
      const body = document.getElementById(toggle.dataset.activityToggle);
      if (body) body.classList.toggle('hidden');
    }
  });
  document.addEventListener('keydown', onPanelKeydown);

  try {
    panel.entries = await listActivity(FETCH_LIMIT);
  } catch (err) {
    panel.error = err.message;
  }
  if (!document.getElementById(PANEL_ID)) return; // closed while the fetch was in flight
  // What the panel showed is now seen: the unread badge clears and the mark moves to the newest entry.
  if (panel.entries) markSeen(document, panel.entries);
  renderControls();
  renderList();
  focusEntry();
  refreshTicks = 0;
  scheduleRefresh();
}

/** Scroll to the entry a toast pointed at, open its detail and flash it. A missing entry (older than the page, or
 *  filtered out) is said, never silently ignored. */
function focusEntry() {
  const id = panel.focusId;
  if (!id) return;
  panel.focusId = '';
  const row = [...document.querySelectorAll('[data-activity-id]')].find((r) => r.dataset.activityId === id);
  const host = document.getElementById('activity-panel-body');
  if (!row) {
    host?.insertAdjacentHTML('afterbegin', '<p data-activity-focus-missing class="mb-s2 text-caveat text-state-warn">That entry is not in the loaded page.</p>');
    return;
  }
  row.scrollIntoView?.({ block: 'center' });
  row.setAttribute('data-activity-focused', '');
  row.classList.add('bg-paper-surface');
  row.querySelector('[data-activity-toggle]')?.click();
}

const ADV_LABEL = { entityType: 'resource', intent: 'stage', operation: 'operation', since: 'since' };
function advCount() { return Object.values(panel.adv).filter(Boolean).length; }
function advActive() { return advCount() > 0; }
function advSelect(key, label, options) {
  return `<label class="flex items-center gap-[4px] text-caveat text-ink-muted">${esc(label)}
    <select data-activity-adv="${key}" aria-label="${esc(ADV_LABEL[key] || label)}"
      class="cursor-pointer rounded-sm border px-2 py-[2px] text-caveat
        ${panel.adv[key] ? 'border-accent text-accent-ink' : 'border-rule text-ink'}">
      ${options.map(([v, l]) => `<option value="${esc(v)}" ${panel.adv[key] === v ? 'selected' : ''}>${esc(l)}</option>`).join('')}
    </select></label>`;
}

function renderControls() {
  const host = document.getElementById('activity-panel-controls');
  if (!host) return;
  const statuses = ['all', 'ok', 'error', 'running'];
  const truncated = panel.entries && panel.entries.length >= FETCH_LIMIT;
  host.innerHTML = `
    <button type="button" id="activity-order-btn" data-activity-order="${panel.order}"
      aria-label="Order by time: ${panel.order === 'asc' ? 'earliest' : 'latest'} first. Press to reverse."
      class="cursor-pointer rounded-sm border border-rule bg-transparent px-2 py-[2px] text-caveat text-ink"
      >${ORDER_LABEL[panel.order]}</button>
    <input id="activity-filter-text" type="text" placeholder="filter by resource or summary…"
      value="${esc(panel.textFilter)}"
      class="min-w-[220px] flex-1 rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink" />
    <span class="flex gap-[6px]">${statuses.map((s) => `<button data-activity-status="${s}"
      aria-pressed="${panel.statusFilter === s ? 'true' : 'false'}"
      class="cursor-pointer rounded-sm border border-rule bg-transparent px-2 py-[2px] text-caveat
        ${panel.statusFilter === s ? 'border-accent text-accent-ink' : 'text-ink-muted hover:text-ink'}"
      >${s}</button>`).join('')}</span>
    <details id="activity-advanced" class="w-full" ${advActive() ? 'open' : ''}>
      <summary class="cursor-pointer text-caveat ${advActive() ? 'text-accent-ink' : 'text-ink-muted'}">filters${
        advActive() ? ` · ${advCount()} set` : ''}</summary>
      <div class="mt-s2 flex flex-wrap items-center gap-s3">
        ${advSelect('entityType', 'resource', [['', 'any'], ...Object.keys(ENTITY_ICON).map((k) => [k, k])])}
        ${advSelect('intent', 'stage', [['', 'any'], ...INTENTS.map((k) => [k, k])])}
        ${advSelect('operation', 'operation', [['', 'any'], ...Object.entries(OPERATION_LABEL).map(([k, v]) => [k, v])])}
        ${advSelect('since', 'since', SINCE_CHOICES)}
        ${advActive() ? '<button type="button" data-activity-adv-clear class="cursor-pointer bg-transparent text-caveat text-accent-ink underline">clear filters</button>' : ''}
      </div>
    </details>
    ${truncated && panel.order === 'asc'
    ? `<div id="activity-order-note" class="w-full text-provenance text-ink-muted">
        <span class="tnum">${panel.entries.length}</span> most recent entries, oldest first</div>`
    : ''}`;
  host.querySelector('#activity-order-btn').addEventListener('click', () => {
    panel.order = panel.order === 'asc' ? 'desc' : 'asc';
    writeOrder(panel.order);
    renderControls();
    renderList();
    document.getElementById('activity-order-btn')?.focus();
  });
  host.querySelector('#activity-filter-text').addEventListener('input', (e) => {
    panel.textFilter = e.target.value;
    renderList();
  });
  host.querySelectorAll('[data-activity-status]').forEach((b) => b.addEventListener('click', () => {
    panel.statusFilter = b.dataset.activityStatus;
    renderControls();
    reload();
  }));
  host.querySelectorAll('[data-activity-adv]').forEach((sel) => sel.addEventListener('change', () => {
    panel.adv[sel.dataset.activityAdv] = sel.value;
    renderControls();
    reload();
  }));
  host.querySelector('[data-activity-adv-clear]')?.addEventListener('click', () => {
    panel.adv = { entityType: '', intent: '', operation: '', since: '' };
    renderControls();
    reload();
  });
}

function filteredEntries() {
  if (!panel.entries) return [];
  const q = panel.textFilter.trim().toLowerCase();
  return panel.entries.filter((e) => {
    if (panel.statusFilter !== 'all' && (e.status || '').toLowerCase() !== panel.statusFilter) return false;
    if (!q) return true;
    return (e.entity_slug || '').toLowerCase().includes(q)
      || (e.entity_name || '').toLowerCase().includes(q)
      || (e.summary || '').toLowerCase().includes(q);
  });
}

/** A run's `detail` is usually a JSON object the logger serialised. Shown as it is stored, it is a wall
 *  of braces. Here: a summary line (the error first, then the first few plain values), one row per key
 *  with nested values compact, and the stored text itself behind a collapsed "raw" disclosure. Text that
 *  is not a JSON object keeps the plain rendering. */
const SUMMARY_KEYS = 4;
const DETAIL_ROW_CAP = 40;
function plainValue(v) {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'object') return Array.isArray(v) ? `${v.length} item${v.length === 1 ? '' : 's'}` : `${Object.keys(v).length} field${Object.keys(v).length === 1 ? '' : 's'}`;
  return String(v);
}
function compactValue(v) {
  if (v === null || v === undefined) return '—';
  if (Array.isArray(v)) return v.length <= 6 && v.every((x) => typeof x !== 'object' || x === null) ? v.map(String).join(', ') || '—' : plainValue(v);
  if (typeof v === 'object') return Object.entries(v).map(([k, x]) => `${k}: ${typeof x === 'object' && x !== null ? plainValue(x) : String(x)}`).join(' · ') || '—';
  return String(v);
}
export function readableDetailHtml(detail) {
  let obj = null;
  try { const parsed = JSON.parse(detail); if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) obj = parsed; } catch { /* not JSON */ }
  if (!obj) {
    return `<div class="mt-s2 whitespace-pre-wrap text-provenance text-ink-muted">${
      detail.split('\n').map((line) => hiGuid(esc(line))).join('<br/>')}</div>`;
  }
  const entries = Object.entries(obj);
  const failing = entries.filter(([k, v]) => /^(error|errors|failure|reason)$/i.test(k) && v !== null && v !== undefined && v !== '' && !(Array.isArray(v) && !v.length));
  const rest = entries.filter(([k, v]) => !failing.some(([fk]) => fk === k) && (v === null || typeof v !== 'object') && v !== null && v !== '');
  const lead = [...failing, ...rest].slice(0, SUMMARY_KEYS);
  const summary = lead.map(([k, v]) => `${esc(k)}: ${hiGuid(esc(plainValue(v)))}`).join(' · ');
  const more = Math.max(0, entries.length - DETAIL_ROW_CAP);
  const rows = entries.slice(0, DETAIL_ROW_CAP).map(([k, v]) => `<tr data-activity-detail-row>
    <td class="w-40 py-[2px] pr-2 align-top text-ink-muted">${esc(k)}</td>
    <td class="py-[2px] font-mono text-ink">${hiGuid(esc(compactValue(v)))}</td></tr>`).join('');
  return `<div class="mt-s2 text-provenance">
    <div data-activity-detail-summary class="text-ink">${summary || 'no fields'}</div>
    <table class="mt-s1 w-full border-collapse"><tbody class="divide-y divide-rule">${rows}</tbody></table>
    ${more ? `<div class="text-ink-muted"><span class="tnum">${more}</span> more in raw</div>` : ''}
    <details data-activity-raw class="mt-s1"><summary class="cursor-pointer text-accent-ink">raw</summary>
      <pre class="mt-[2px] max-h-48 overflow-auto whitespace-pre-wrap break-all font-mono text-ink-muted">${esc(JSON.stringify(obj, null, 2))}</pre></details>
  </div>`;
}

function entryRowHtml(op) {
  const opLabel = OPERATION_LABEL[op.operation] || op.operation || 'Operation';
  const opIcon = OPERATION_ICON[op.operation] || '•';
  const entityIcon = ENTITY_ICON[op.entity_type] || '';
  const tone = STATUS_TONE[(op.status || '').toLowerCase()] || 'text-ink-muted';
  const glyph = STATUS_GLYPH[(op.status || '').toLowerCase()] || '·';
  const when = op.ts ? ago(op.ts) : '';
  const whenTitle = (op.ts || '').replace('T', ' ').slice(0, 19) + ' UTC';

  const annotations = op.annotations || [];
  const annHtml = annotations.length
    ? `<div class="mt-[3px] text-provenance text-ink-muted">${
        annotations.slice(0, 6).map((a) => `${esc(a.analysis_name || a.annotation_type || '')}: ${tnum(String(a.count ?? ''))}`).join(' · ')
      }${annotations.length > 6 ? ` · +${annotations.length - 6} more` : ''}</div>`
    : '';

  const tableItems = (op.items || []).filter((it) => !it.link);
  const itemsHtml = tableItems.length
    ? `<table class="mt-s2 w-full border-collapse text-provenance">
        <thead><tr class="text-ink-muted"><th class="w-28 pb-[2px] text-left font-normal">Type</th>
          <th class="pb-[2px] text-left font-normal">Name</th>
          <th class="w-36 pb-[2px] text-left font-normal">GUID</th></tr></thead>
        <tbody class="divide-y divide-rule">${tableItems.map((it) => `<tr>
          <td class="py-[2px] pr-2 text-ink-muted">${esc(it.kind || '')}</td>
          <td class="max-w-[16rem] truncate py-[2px] pr-2 font-mono text-ink" title="${esc(it.display_name || it.name || '')}">${esc(it.display_name || it.name || '—')}</td>
          <td class="py-[2px]">${it.guid ? hiGuid(esc(it.guid)) : '<span class="text-ink-muted">—</span>'}</td>
        </tr>`).join('')}</tbody></table>`
    : '';

  const detailHtml = op.detail ? readableDetailHtml(op.detail) : '';

  const hasDetail = !!(annHtml || itemsHtml || detailHtml);
  const detailId = `activity-d-${esc(op.id || '').replace(/[^a-z0-9]/gi, '_').slice(0, 24)}`;

  return `<div class="border-b border-rule py-s2" data-activity-id="${esc(op.id || '')}">
    <div class="flex flex-wrap items-baseline gap-[6px]">
      <span class="shrink-0">${opIcon}</span>
      <span class="font-heading text-answer text-ink">${esc(opLabel)}</span>
      ${op.entity_slug ? `<span class="font-mono text-provenance text-ink-muted">${entityIcon} ${esc(op.entity_slug)}</span>` : ''}
      ${op.intent ? `<span class="text-provenance text-ink-muted">${esc(op.intent)}</span>` : ''}
      <span class="ml-auto shrink-0 text-provenance ${tone}">${glyph} ${esc(op.status || '')}</span>
      ${op.publish_proof ? publishProofHtml(op.publish_proof) : ''}
      ${hasDetail ? `<button type="button" data-activity-toggle="${detailId}"
        class="cursor-pointer bg-transparent p-0 text-provenance text-accent-ink" title="Show detail">▶ detail</button>` : ''}
    </div>
    <div class="mt-[2px] text-caveat text-ink">${esc(op.summary || '')}</div>
    <div class="mt-[2px] text-provenance text-ink-muted" title="${esc(whenTitle)}">${esc(when)}</div>
    ${hasDetail ? `<div id="${detailId}" class="hidden">${annHtml}${itemsHtml}${detailHtml}</div>` : ''}
  </div>`;
}

function renderList() {
  const host = document.getElementById('activity-panel-body');
  if (!host) return;

  if (panel.error) {
    host.innerHTML = `<p class="max-w-[60ch] text-answer text-accent-ink">The activity log could
      not be read: ${esc(panel.error)}</p>`;
    return;
  }
  if (panel.entries === null) {
    host.innerHTML = '<p class="text-caveat text-ink-muted">reading…</p>';
    return;
  }

  const rows = filteredEntries();
  if (!panel.entries.length && (advActive() || panel.statusFilter !== 'all')) {
    host.innerHTML = `<p data-activity-none-match class="max-w-[60ch] text-answer text-ink">No recorded
      operation matches these filters.</p>`;
    return;
  }
  if (!panel.entries.length) {
    host.innerHTML = `<p class="max-w-[60ch] text-answer text-ink">No operations recorded yet.
      Scouting, running a survey, or publishing to Egeria all write here. It is
      empty because nothing has happened yet, not because logging is broken.</p>`;
    return;
  }
  if (!rows.length) {
    host.innerHTML = `<p class="max-w-[60ch] text-answer text-ink">Nothing recorded matches
      this filter. <span class="tnum">${panel.entries.length}</span> total entr${panel.entries.length === 1 ? 'y is' : 'ies are'}
      loaded; try a different filter or status.</p>`;
    return;
  }
  const shownOfLoaded = rows.length < panel.entries.length
    ? `<div class="mb-s2 text-provenance text-ink-muted"><span class="tnum">${rows.length}</span> of
       <span class="tnum">${panel.entries.length}</span> loaded entries match this filter.</div>`
    : (panel.entries.length >= FETCH_LIMIT
      ? `<div class="mb-s2 text-provenance text-ink-muted">Showing the most recent
         <span class="tnum">${FETCH_LIMIT}</span> entries.</div>`
      : '');
  host.innerHTML = shownOfLoaded + sortByTime(rows, panel.order).map(entryRowHtml).join('');
}
