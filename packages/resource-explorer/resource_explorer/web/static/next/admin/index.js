/* Admin — system/catalog configuration, reachable from the header's own
 * ⚙ Admin button (PLAN-FINISH-REPOS.md item 5).
 *
 * Per packages/resource-explorer/CLAUDE.md's "System/catalog configuration
 * is not an intent" section, Admin is chrome-level and decoupled from
 * `#intent-nav`/`currentNavIntent` — the same pattern as 📋 Activity
 * (next/stages/activity.js). It is NOT one of app.js's STAGES entries.
 *
 * SCOPE. Classic's Admin (index.html, `_ADMIN_GROUPS`) groups eleven panes
 * into three groups by "what a person came to do" (commit 4fb48071/26320f89):
 *
 *   Configure  Annotation Types, Groups, Repository discovery sources, Question Catalog
 *   Reconcile  Egeria Alignment, Egeria Links, Publish Queue, Repair
 *   Observe    Prefect, Feedback, Logs
 *
 * (packages/resource-explorer/CLAUDE.md's own "Annotation Types registry,
 * resource Groups, and the Schedules overview" description is now stale on
 * two counts: Schedules moved to Automate's own sub-tab on 2026-08-13, and
 * Feedback/Logs were added under Observe on 2026-08-31/09-01 — eleven panes
 * total today, not the "ten classic admin views" PLAN-FINISH-REPOS.md
 * expected; see ITEM-5-ADMIN-IMPLEMENTED.md for the reconciliation.)
 *
 * Nine panes are real ports here — Logs, Feedback and Prefect are
 * read-only/bounded read+status-action surfaces; Groups gained
 * create/delete/assign/suggestions (SPEC-ADMIN-THE-FOUR-GAPS.md §2 — see
 * next/admin/groups.js and GROUPS-ADMIN-IMPLEMENTED.md); Discovery Sources
 * gained full CRUD + preview-then-apply refresh + preview-then-import run
 * (see discovery_sources.js's own header and
 * DISCOVERY-SOURCES-ADMIN-IMPLEMENTED.md); Egeria
 * Alignment and Repair (§1) are built as TWO separate panes because classic
 * has them as two separate screens doing different jobs — see
 * admin/resync.js and admin/repair.js's own header comments for why folding
 * them together would be wrong; and **Annotation Types and Question Catalog
 * gained real write UI on 2026-09-20** (§4 — "the two registries"):
 * Annotation Types now creates/edits/deletes against routes that already
 * existed (annotation_types.js), and Question Catalog gained a new,
 * append-only add/retire backend (question_catalog_writer.py) alongside its
 * UI — see that spec and ADMIN-REGISTRIES-IMPLEMENTED.md for what changed
 * and why editing a question's own text is refused rather than offered.
 * Egeria Links (PI-130) and Publish Queue (PI-131) were the two named
 * deferrals; both are built now (admin/egeria_links.js, admin/outbox.js). The
 * Publish Queue never offers a retry on a write that archives, deletes or
 * detaches, and Egeria Links offers no delete at all. Every tab has a `render`;
 * the not-built page that served a tab without one was removed as unreachable.
 */
import { $, esc } from '/static/next/app.js';
import { renderAnnotationTypes } from '/static/next/admin/annotation_types.js';
import { renderQuestionCatalog } from '/static/next/admin/question_catalog.js';
import { renderLogs } from '/static/next/admin/logs.js';
import { renderFeedback } from '/static/next/admin/feedback.js';
import { renderPrefect } from '/static/next/admin/prefect.js';
import { renderGroups } from '/static/next/admin/groups.js';
import { renderResync } from '/static/next/admin/resync.js';
import { renderRepair } from '/static/next/admin/repair.js';
import { renderDiscoverySources } from '/static/next/admin/discovery_sources.js';
import { renderEgeriaLinks } from '/static/next/admin/egeria_links.js';
import { renderOutbox } from '/static/next/admin/outbox.js';
import { renderApiFetch } from '/static/next/admin/api_fetch.js';

const PANEL_ID = 'admin-panel';

/** Same grouping and order as classic's `_ADMIN_GROUPS` — a person who knows
 *  the current UI should find the same three groups here, not a reshuffled
 *  version of them. Every tab carries its own `render`. */
const GROUPS = [
  { name: 'Configure', tabs: [
    { id: 'annotations', label: '📝 Annotation Types', render: renderAnnotationTypes },
    { id: 'admin-groups', label: '🗂 Groups', render: renderGroups },
    { id: 'admin-discovery-sources', label: '🔍 Repository discovery sources', render: renderDiscoverySources },
    { id: 'admin-question-catalog', label: '❓ Question Catalog', render: renderQuestionCatalog },
    { id: 'admin-api-fetch', label: '🌐 API fetch addresses', render: renderApiFetch },
  ]},
  { name: 'Reconcile', tabs: [
    { id: 'admin-resync', label: '🔄 Egeria Alignment', render: renderResync },
    { id: 'admin-egeria-links', label: '🔗 Egeria Links', render: renderEgeriaLinks },
    { id: 'admin-outbox', label: '📤 Publish Queue', render: renderOutbox },
    { id: 'admin-repair', label: '🔧 Repair', render: renderRepair },
  ]},
  { name: 'Observe', tabs: [
    { id: 'admin-prefect', label: '⚡ Prefect', render: renderPrefect },
    { id: 'admin-feedback', label: '💬 Feedback', render: renderFeedback },
    { id: 'admin-logs', label: '📜 Logs', render: renderLogs },
  ]},
];

const ALL_TABS = GROUPS.flatMap((g) => g.tabs);

/** Local, per-open state — not persisted, not on app.js's `state`. Mirrors
 *  activity.js's `panel` object: this is view state for one overlay, not
 *  something any other pane reads. */
const panel = { tab: 'annotations' };

function closeAdminPanel() {
  document.getElementById(PANEL_ID)?.remove();
  document.removeEventListener('keydown', onPanelKeydown);
}

function onPanelKeydown(e) {
  if (e.key === 'Escape') closeAdminPanel();
}

function groupSelectHtml(group) {
  const holdsActive = group.tabs.some((t) => t.id === panel.tab);
  const active = group.tabs.find((t) => t.id === panel.tab);
  return `<label class="flex items-center gap-[6px]">
    <span class="text-caps uppercase tracking-caps ${holdsActive ? 'text-accent-ink' : 'text-ink-muted'}">${esc(group.name)}</span>
    <select data-admin-group="${esc(group.name)}"
      title="${esc(group.name)}${holdsActive ? ` — showing ${esc(active.label)}` : ''}"
      class="cursor-pointer rounded-sm border px-2 py-[2px] text-caveat
        ${holdsActive ? 'border-accent text-accent-ink' : 'border-rule-strong text-ink-muted hover:text-ink'}">
      <option value="" ${holdsActive ? '' : 'selected'} disabled>${esc(group.name)}…</option>
      ${group.tabs.map((t) => `<option value="${esc(t.id)}" ${t.id === panel.tab ? 'selected' : ''}>${esc(t.label)}</option>`).join('')}
    </select>
  </label>`;
}

function renderSubnav() {
  const host = document.getElementById('admin-panel-subnav');
  if (!host) return;
  host.innerHTML = GROUPS.map(groupSelectHtml).join('');
  host.querySelectorAll('[data-admin-group]').forEach((sel) => sel.addEventListener('change', () => {
    if (!sel.value) return;
    panel.tab = sel.value;
    renderSubnav();
    renderBody();
  }));
}

async function renderBody() {
  const host = document.getElementById('admin-panel-body');
  if (!host) return;
  const tab = ALL_TABS.find((t) => t.id === panel.tab);
  if (!tab) { host.innerHTML = '<p class="text-answer text-ink-muted">Unknown pane.</p>'; return; }
  host.innerHTML = '<p class="text-answer text-ink-muted">Loading…</p>';
  try {
    await tab.render(host);
  } catch (err) {
    host.innerHTML = `<p class="max-w-[70ch] text-answer text-state-warn">Could not load ${
      esc(tab.label)}: ${esc(err.message)}</p>`;
  }
}

/** Open the panel, freshly on every open — same reasoning as
 *  openActivityPanel: state (schedules, logs, feedback) keeps changing while
 *  the panel is closed, and reopening on the last-fetched data would misreport
 *  it. */
export async function openAdminPanel() {
  closeAdminPanel();
  panel.tab = 'annotations';

  const el = document.createElement('div');
  el.id = PANEL_ID;
  el.className = 'fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-s4 overflow-auto';
  el.innerHTML = `
    <div class="mt-[3vh] w-full max-w-[920px] rounded bg-paper p-s4 shadow-lg"
      role="dialog" aria-modal="true" aria-label="Admin">
      <div class="flex items-start justify-between gap-s3">
        <div>
          <div class="font-heading text-name text-ink">⚙ Admin</div>
          <div class="mt-[2px] text-caveat text-ink-muted">System configuration — not
            something a user does to curate a specific resource, so it lives here rather than
            under one of the eight intents.</div>
        </div>
        <button type="button" data-act="close" class="text-ink-muted hover:text-ink" aria-label="Close">×</button>
      </div>
      <div class="my-s3 h-px bg-rule"></div>
      <div id="admin-panel-subnav" class="mb-s3 flex flex-wrap items-center gap-s4"></div>
      <div id="admin-panel-body" class="max-h-[64vh] overflow-y-auto text-caveat text-ink-muted"></div>
    </div>`;
  document.body.appendChild(el);
  el.addEventListener('click', (e) => {
    if (e.target === el || e.target.closest('[data-act="close"]')) closeAdminPanel();
  });
  document.addEventListener('keydown', onPanelKeydown);

  renderSubnav();
  await renderBody();
}
