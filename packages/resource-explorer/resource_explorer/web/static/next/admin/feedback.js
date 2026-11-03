/* Admin → Feedback — both feedback stores, badged by origin.
 *
 * Full port of classic's `loadAdminFeedbackPanel` against the same GET
 * /api/curate/feedback (see curate.py's `list_all_feedback` — it already
 * combines resource_feedback and the page-level `feedback` table, each row
 * carrying `source: "page" | "resource"`, precisely because a 2026-09-01
 * incident found a page-level submission rendering as "no feedback" when the
 * pane simply never read that store).
 *
 * The route is admin-gated (`_require_admin` in curate.py, matching
 * routes/feedback.py's `X-Admin-Token` check) because it can surface contact
 * fields on the resource side. This module reproduces classic's gate exactly
 * — same sessionStorage key (`re_admin_token`), same header — rather than
 * inventing a second mechanism: entering the token in either surface
 * authenticates both. This is NOT routed through re-api.js's `request()`,
 * because a 403 here is a distinct, expected state (not authenticated yet)
 * that must render its own explanation rather than the generic ApiError
 * path — an unauthenticated view and a genuinely empty store must not look
 * alike.
 */
import { esc } from '/static/next/app.js';

const TOKEN_KEY = 're_admin_token';

const filter = { entity_type: '', category: '', source: '' };
let _host = null;

function authHeaders() {
  const t = sessionStorage.getItem(TOKEN_KEY) || '';
  return t ? { 'X-Admin-Token': t } : {};
}

function ratingHtml(r) {
  if (r === null || r === undefined) {
    return '<span class="text-ink-muted" title="No rating given">—</span>';
  }
  return `<span class="text-state-warn">${'★'.repeat(r)}<span class="text-ink-muted">${'★'.repeat(Math.max(0, 5 - r))}</span></span>`;
}

function sourceBadge(source) {
  return source === 'page'
    ? '<span class="rounded-sm border border-rule-strong px-[6px] py-[1px] text-provenance text-ink" title="feedback table — the page-level widget, reachable from anywhere in the app">page</span>'
    : '<span class="rounded-sm border border-accent-deep px-[6px] py-[1px] text-provenance text-accent-ink" title="resource_feedback table — recorded from a resource\'s Curate tab">resource</span>';
}

function tokenGateHtml() {
  return `
    <h3 class="m-0 font-heading text-name font-normal text-ink">Admin credential required</h3>
    <div class="my-s3 h-px bg-rule"></div>
    <p class="max-w-[60ch] text-answer text-ink">This view reads submitted feedback, which can
      include contact details, so it is gated. <strong>This is not an empty list</strong> — it is
      a view you are not yet authenticated for.</p>
    <div class="mt-s3 flex max-w-[420px] gap-s2">
      <input data-token-input type="password" placeholder="Admin token"
        class="flex-1 rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] text-caveat text-ink" />
      <button type="button" data-token-connect
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[3px] text-caveat text-accent-ink">Connect</button>
    </div>
    <p class="mt-s2 max-w-[60ch] text-provenance text-ink-muted">Held in sessionStorage for this
      tab only, and shared with <code>/admin/feedback</code> and the current UI's Admin → Feedback.</p>`;
}

function chip(label, key, value) {
  const active = filter[key] === value;
  return `<button type="button" data-filter-key="${esc(key)}" data-filter-val="${esc(value)}"
    class="cursor-pointer rounded-sm border px-2 py-[2px] text-caveat
      ${active ? 'border-accent text-accent-ink' : 'border-rule-strong text-ink-muted hover:text-ink'}"
    >${esc(label)}</button>`;
}

const LOOK_NAMES = [['dark', 'Dark'], ['light', 'Light'], ['paper', 'Paper']];

/** Which look people use, from the activity rows each change writes (RULING-THEMES-PORTAL-DARK-LIGHT.md section 1).
 *  Says what the numbers cannot know: someone who never touched the control is on Dark and leaves no row. A fetch
 *  that fails is said to have failed, not shown as zeros. */
async function lookCountsHtml() {
  let data;
  try {
    const res = await fetch('/api/feedback/look-counts', { headers: authHeaders() });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (err) {
    return `<p class="mt-s3 text-caveat text-state-warn" data-look-counts-error>Could not load the look counts: ${esc(err.message)}</p>`;
  }
  const rows = LOOK_NAMES.map(([id, name]) => {
    const c = (data.looks || {})[id] || { people: 0, switched_away: 0 };
    return `<tr class="border-b border-rule" data-look-row="${id}">
      <td class="py-[3px] pr-s4 text-ink">${name}</td>
      <td class="tnum py-[3px] pr-s4 text-ink">${c.people}</td>
      <td class="tnum py-[3px] text-ink">${c.switched_away}</td></tr>`;
  }).join('');
  return `<div class="mt-s3" data-look-counts>
    <h4 class="m-0 mb-[2px] font-heading text-subtab font-normal text-ink">Which look people use</h4>
    <table class="text-caveat"><thead><tr class="text-caps uppercase tracking-caps text-ink-muted">
      <th class="pr-s4 text-left font-normal">Look</th>
      <th class="pr-s4 text-left font-normal">People on it now</th>
      <th class="text-left font-normal">Switched away from it</th></tr></thead>
      <tbody>${rows}</tbody></table>
    <p class="mt-[2px] max-w-[65ch] text-provenance text-ink-muted">Counts people who have changed the look at least once
      (<span class="tnum">${data.people_who_changed || 0}</span> people, <span class="tnum">${data.changes || 0}</span> changes).
      Anyone who never touched the control is on Dark and leaves no row, so Dark's number is a floor, not a total.
      Comments sent from the look menu are in the list below, subject "Look: ...".</p></div>`;
}

async function reload() {
  if (!_host) return;
  const params = new URLSearchParams();
  if (filter.entity_type) params.set('entity_type', filter.entity_type);
  if (filter.category) params.set('category', filter.category);
  if (filter.source) params.set('source', filter.source);

  let res;
  try {
    res = await fetch(`/api/curate/feedback?${params}`, { headers: authHeaders() });
  } catch (err) {
    _host.innerHTML = `<p class="max-w-[70ch] text-answer text-state-warn">Could not load feedback: ${esc(err.message)}</p>`;
    return;
  }
  if (res.status === 403) {
    _host.innerHTML = tokenGateHtml();
    bindTokenGate();
    return;
  }
  if (!res.ok) {
    _host.innerHTML = `<p class="max-w-[70ch] text-answer text-state-warn">Could not load feedback: HTTP ${res.status}</p>`;
    return;
  }
  const data = await res.json();

  const rows = data.feedback || [];
  const counts = data.counts || { resource: { total: 0, resources: 0 }, page: { total: 0 } };
  const total = (counts.resource?.total || 0) + (counts.page?.total || 0);
  const types = [...new Set(rows.filter((r) => r.source === 'resource').map((r) => r.entity_type))].sort();
  const cats = [...new Set(rows.map((r) => r.category).filter(Boolean))].sort();

  let empty;
  if (data.filtered) {
    empty = `<p class="py-s4 text-center text-caveat text-ink-muted">No feedback matches this filter.
      Clear it to see all ${total} (${counts.page?.total || 0} page, ${counts.resource?.total || 0} resource).</p>`;
  } else if (total === 0) {
    empty = `<p class="py-s4 text-center text-caveat text-ink-muted">No feedback has been left anywhere yet.<br>
      <span class="text-ink-muted">Neither the page-level widget nor any resource's Curate tab has a row.</span></p>`;
  } else {
    empty = '<p class="py-s4 text-center text-caveat text-ink-muted">Nothing rendered for this page, though feedback exists — try Refresh.</p>';
  }

  const body = rows.length ? `<table class="w-full text-left">
    <thead><tr class="border-b border-rule text-caps uppercase tracking-caps text-ink-muted">
      <th class="pb-s2 pr-s3 font-normal">When</th>
      <th class="pb-s2 pr-s3 font-normal">Source</th>
      <th class="pb-s2 pr-s3 font-normal">Resource / Page</th>
      <th class="pb-s2 pr-s3 font-normal">Category</th>
      <th class="pb-s2 pr-s3 font-normal">Rating</th>
      <th class="pb-s2 pr-s3 font-normal">Message</th>
      <th class="pb-s2 font-normal">Status</th>
    </tr></thead>
    <tbody>${rows.map((r) => {
      const isPage = r.source === 'page';
      const context = isPage
        ? (r.page ? `<span class="font-mono text-ink">${esc(r.page)}</span>` : '<span class="text-ink-muted" title="No page recorded with this submission">—</span>')
        : `<span class="text-ink-muted">${esc(r.entity_type || '')}</span> <span class="font-mono text-ink">${esc(r.entity_slug || '')}</span>`;
      const status = isPage
        ? esc(r.triage_status || 'new')
        : '<span class="italic text-ink-muted" title="resource_feedback has no triage workflow">n/a</span>';
      return `<tr class="border-b border-rule hover:bg-paper-surface">
        <td class="whitespace-nowrap py-s2 pr-s3 font-mono text-provenance text-ink-muted">${esc((r.created_at || '').slice(0, 16).replace('T', ' '))}</td>
        <td class="whitespace-nowrap py-s2 pr-s3">${sourceBadge(r.source)}</td>
        <td class="whitespace-nowrap py-s2 pr-s3">${context}</td>
        <td class="py-s2 pr-s3 text-caveat text-ink-muted">${esc(r.category || '—')}</td>
        <td class="whitespace-nowrap py-s2 pr-s3">${ratingHtml(r.rating)}</td>
        <td class="max-w-[32ch] py-s2 pr-s3 text-caveat text-ink">${esc(r.message || '')}</td>
        <td class="whitespace-nowrap py-s2 text-caveat">${status}</td>
      </tr>`;
    }).join('')}</tbody></table>` : empty;

  _host.innerHTML = `
    <h3 class="m-0 font-heading text-name font-normal text-ink">💬 Feedback</h3>
    <p class="mt-[2px] max-w-[65ch] text-caveat text-ink-muted">
      ${counts.page?.total || 0} page item${(counts.page?.total || 0) === 1 ? '' : 's'} (the widget on
      any page) + ${counts.resource?.total || 0} resource item${(counts.resource?.total || 0) === 1 ? '' : 's'}
      across ${counts.resource?.resources || 0} resource${(counts.resource?.resources || 0) === 1 ? '' : 's'}
      (a resource's Curate tab) = ${total} total. Shown together, badged by origin — not merged.
    </p>
    ${await lookCountsHtml()}
    <div class="my-s3 flex flex-wrap items-center gap-[6px]">
      ${chip('All sources', 'source', '')}
      ${chip('Page', 'source', 'page')}
      ${chip('Resource', 'source', 'resource')}
      <span class="px-[2px] text-ink-muted">|</span>
      ${chip('All resource types', 'entity_type', '')}
      ${types.map((t) => chip(t, 'entity_type', t)).join('')}
      ${cats.length ? '<span class="px-[2px] text-ink-muted">|</span>' : ''}
      ${cats.length ? chip('Any category', 'category', '') : ''}
      ${cats.map((c) => chip(c, 'category', c)).join('')}
    </div>
    <div class="max-h-[46vh] overflow-auto rounded-sm border border-rule">${body}</div>`;

  bindFilters();
}

function bindFilters() {
  if (!_host) return;
  _host.querySelectorAll('[data-filter-key]').forEach((b) => b.addEventListener('click', () => {
    // Setting entity_type has no meaning for page-sourced rows; clearing an
    // active page-only source filter here would silently show rows that
    // don't support the entity_type facet — leave source alone and let the
    // empty state say so, same as classic.
    filter[b.dataset.filterKey] = b.dataset.filterVal;
    reload();
  }));
}

function bindTokenGate() {
  if (!_host) return;
  const connect = _host.querySelector('[data-token-connect]');
  const input = _host.querySelector('[data-token-input]');
  if (!connect || !input) return;
  const submit = () => {
    const t = input.value.trim();
    if (!t) return;
    sessionStorage.setItem(TOKEN_KEY, t);
    reload();
  };
  connect.addEventListener('click', submit);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
}

export async function renderFeedback(host) {
  _host = host;
  filter.entity_type = '';
  filter.category = '';
  filter.source = '';
  await reload();
}
