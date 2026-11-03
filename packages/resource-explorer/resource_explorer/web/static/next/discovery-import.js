/* Discovery import — corpus-level "find and import candidate repos".
 *
 * NOT a stage module, deliberately: SPEC-ACTIONABLE-AND-HONEST.md point 2
 * (project owner's round, 2026-09-15) found that `Find repos` had been
 * placed as a per-stage sub-tab and ruled that wrong — searching/importing
 * repos happens BEFORE any stage applies and is the same action on
 * Scouting as on Curate, so it does not belong to Discovery any more than
 * to any other stage. It now lives beside the sidebar's `Repos · DBs · FS`
 * switcher (`app.js`'s `find-repos` sidebar action), which is the control
 * that already scopes the whole left column — this module is what that
 * action opens, replacing the old "open the current UI" stub for
 * `state.resourceType === 'repo'`.
 *
 * This is the real port of classic's (`index.html`) three corpus-level
 * Discovery affordances `stages/discovery.js`'s header comment named as not
 * yet ported: repo search (`searchScoutRepos()`), the `/from-list` bulk
 * loader (`_loadRepoListFromFile()`), and the inventory CSV export
 * (`_downloadInventory()`). Org import folds into search (`RepoSearchRequest.org`
 * is a plain qualifier field, same as classic) plus from-list's own
 * account-URL expansion (`_expand_org`, server-side) — there is no separate
 * "org import" affordance in the backend to port a third UI for.
 *
 * Same two-step shape as classic throughout: searching or loading a list
 * only produces REVIEW rows (`DiscoveredRepo`) — nothing is registered
 * until rows are selected and "Import selected" is pressed. Disposition
 * (👁 track / 🔬 investigate / 👍 recommend / ✅ using / ⛔ abandon / 🚫
 * ignore) can be set on a row before it's ever imported, exactly as
 * classic's per-row disposition buttons do, because `repo_dispositions`
 * keys on `github_url`, not on a registered project.
 */
import { openDialog } from '/static/next/worklist.js';
import { inPagePrompt } from '/static/next/confirm-dialog.js';
import {
  searchDiscoveryRepos, discoverFromList, fetchInventoryCsv, importDiscoveredRepos,
  setDisposition, listGroups, listFoundations, getImportStatus,
} from '/static/re-api.js';
import { esc, icon, refreshGroupsAndSidebar } from '/static/next/app.js';
import { noticeActivity } from '/static/next/stages/activity.js';

// Module-local view state for the one dialog this file renders — mirrors
// classic's own module-level `_scout*` globals, scoped here instead of
// polluting app.js's shared `state` with a feature only this dialog reads.
const view = {
  mode: 'search',        // 'search' | 'list'
  filters: {
    keyword: '', min_stars: 0, language: '', license: '', pushed_after: '',
    org: '', topic: '', include_archived: false, include_forks: false,
  },
  results: [],            // DiscoveredRepo[]
  resultsSource: '',       // provenance line ("GitHub search", a filename)
  selected: new Set(),     // indices into `results`
  filterText: '',
  groups: [],
  foundations: null,       // {key: {label, org?, topic?}} once read; null = not read
  foundationsError: '',
  status: '',              // feedback line above the results table
  statusIsError: false,
  listStatus: '',          // separate feedback line for the from-list panel
  listStatusIsError: false,
  busy: false,
  importing: false,        // a batch is in flight: Import is pressed and disabled
  importState: new Map(),  // github_url -> importing | imported | already_registered | failed
};

/** How often an in-flight batch is re-read, and the most ticks it is followed for. Tests shorten it. */
export const importPollConfig = { intervalMs: 2000, maxTicks: 900 };

const STATE_WORD = {
  importing: '◔ importing',
  imported: '✓ imported',
  already_registered: '✓ already registered',
  failed: '✗ failed',
};

const DISPOSITION_EMOJI = {
  tracking: '👁', investigating: '🔬', recommended: '👍',
  using: '✅', abandoned: '⛔', ignored: '🚫',
};
const HIDDEN_DISPOSITIONS = new Set(['abandoned', 'ignored']);

/** Opens the dialog and kicks off the first render. The only export — every
 *  other function here is reached only from within the dialog it builds. */
export async function openFindReposDialog() {
  const el = openDialog('Find and import candidate repos',
    'Search GitHub or load a list — nothing is registered until you select rows below',
    { wide: true });
  try { view.groups = await listGroups(); } catch { view.groups = []; }
  try { view.foundations = await listFoundations(); view.foundationsError = ''; }
  catch (err) { view.foundations = null; view.foundationsError = err.message || 'could not be read'; }
  render(el);
}

function render(el) {
  const body = el.querySelector('#wl-detail-body');
  body.innerHTML = `
    ${modeTabsHtml()}
    ${view.mode === 'search' ? searchFormHtml() : listPanelHtml()}
    ${statusLineHtml(view.status, view.statusIsError)}
    <div class="my-s3 h-px bg-rule"></div>
    ${resultsHtml()}
  `;
  bind(el);
}

function statusLineHtml(text, isError) {
  if (!text) return '';
  return `<p class="mt-s2 text-caveat ${isError ? 'text-state-warn' : 'text-ink-muted'}">${esc(text)}</p>`;
}

function modeTabsHtml() {
  const tab = (id, label) => `<button data-mode="${id}"
    class="cursor-pointer border-0 bg-transparent pb-[2px] mr-s4 font-heading text-subtab ${
      view.mode === id ? 'border-b border-accent text-ink' : 'text-ink-muted hover:text-ink'}"
    >${esc(label)}</button>`;
  return `<div class="mb-s3 flex items-center gap-0 text-subtab">
    ${tab('search', '🔍 Search GitHub')}${tab('list', '📄 From a list')}
  </div>`;
}

/* ── Search GitHub ──────────────────────────────────────────────────────── */

function searchFormHtml() {
  const f = view.filters;
  const field = (id, label, extra = '') => `<div>
    <label class="mb-[2px] block text-caps uppercase tracking-caps text-ink-muted">${esc(label)}</label>
    ${extra}
  </div>`;
  const input = (key, placeholder = '', type = 'text') =>
    `<input data-f="${key}" type="${type}" placeholder="${esc(placeholder)}" value="${esc(f[key] ?? '')}"
       class="w-full rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink">`;
  return `
    ${foundationChipsHtml()}
    <p class="mb-s2 max-w-[70ch] text-caveat text-ink-muted">
      A general GitHub search for candidate repos — independent of any specific org. To pull in a
      whole account's repos, put an org/user search filter here, or paste an account URL
      (e.g. <span class="font-mono">github.com/apache</span>) into "From a list" and it expands
      automatically.
    </p>
    <div class="grid grid-cols-2 gap-s2 sm:grid-cols-4">
      ${field('keyword', 'Keyword', input('keyword', 'data catalog'))}
      ${field('min_stars', 'Min stars', input('min_stars', '0', 'number'))}
      ${field('language', 'Language', input('language', 'python'))}
      ${field('license', 'License', input('license', 'apache-2.0'))}
      ${field('pushed_after', 'Pushed after', input('pushed_after', '', 'date'))}
      ${field('org', 'Org', input('org', 'my-org'))}
      ${field('topic', 'Topic', input('topic', 'data-catalog'))}
    </div>
    <div class="mt-s2 flex flex-wrap items-center gap-s3 text-caveat text-ink-muted">
      <label class="flex items-center gap-[4px]"><input type="checkbox" data-f="include_archived" ${f.include_archived ? 'checked' : ''}> Include archived</label>
      <label class="flex items-center gap-[4px]"><input type="checkbox" data-f="include_forks" ${f.include_forks ? 'checked' : ''}> Include forks</label>
      <button data-act="search" class="ml-auto cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[3px] text-caveat text-accent-ink" ${view.busy ? 'disabled' : ''}
        >${view.busy ? 'Searching…' : 'Search'}</button>
    </div>`;
}

/** A foundation pre-filter fills the Org and Topic the foundation is known by. It is a starting point for the
 *  search form, not a separate search: the fields stay editable, and pressing the chip runs the search
 *  (classic's behaviour). A foundation that has not been read says so; an empty list says none are configured. */
function foundationChipsHtml() {
  const f = view.filters;
  const label = '<span class="text-caps uppercase tracking-caps text-ink-muted">Foundation</span>';
  if (view.foundations === null) {
    return `<div data-foundations class="mb-s2 flex flex-wrap items-baseline gap-s2 text-caveat">${label}
      <span data-foundations-unread class="text-state-warn" title="${esc(view.foundationsError)}"><span class="font-glyph" aria-hidden="true">?</span> not read</span>
      <span class="text-ink-muted">${esc(view.foundationsError ? `the foundation list could not be read: ${view.foundationsError}` : 'the foundation list has not been read')}</span></div>`;
  }
  const entries = Object.entries(view.foundations);
  if (!entries.length) {
    return `<div data-foundations class="mb-s2 flex flex-wrap items-baseline gap-s2 text-caveat">${label}
      <span class="text-ink-muted">none configured</span></div>`;
  }
  return `<div data-foundations class="mb-s2 flex flex-wrap items-baseline gap-s2 text-caveat">${label}
    ${entries.map(([key, fd]) => {
      const on = (fd.org || '') === (f.org || '') && (fd.topic || '') === (f.topic || '') && !!(fd.org || fd.topic);
      return `<button type="button" data-foundation="${esc(key)}" aria-pressed="${on ? 'true' : 'false'}"
        title="${on ? 'Filling the Org and Topic fields with this foundation. Press again to clear them.' : 'Fill Org and Topic with this foundation and search'}"
        class="cursor-pointer rounded-pill border bg-transparent px-[8px] py-[1px] ${on ? 'border-accent text-ink' : 'border-rule text-accent-ink'}">${on ? '● ' : ''}${esc(fd.label || key)}</button>`;
    }).join('')}
    ${f.org || f.topic ? '' : '<span class="text-ink-muted">press one to fill Org and Topic and search</span>'}
  </div>`;
}

function readFiltersFromDom(el) {
  el.querySelectorAll('[data-f]').forEach((inp) => {
    const key = inp.dataset.f;
    if (inp.type === 'checkbox') view.filters[key] = inp.checked;
    else if (inp.type === 'number') view.filters[key] = parseInt(inp.value, 10) || 0;
    else view.filters[key] = inp.value.trim();
  });
  // GitHub topics are always lowercase-hyphenated — same normalization
  // classic's `_currentScoutFilters()` applies, so a human-typed "Data
  // Catalog" becomes the valid qualifier "data-catalog" instead of a 400.
  view.filters.topic = view.filters.topic.toLowerCase().replace(/\s+/g, '-');
}

const NON_FILTER_KEYS = new Set(['min_stars', 'include_archived', 'include_forks']);

async function runSearch(el) {
  readFiltersFromDom(el);
  const hasFilter = Object.entries(view.filters).some(([k, v]) =>
    NON_FILTER_KEYS.has(k) ? (k === 'min_stars' && v > 0) : !!v);
  if (!hasFilter) {
    view.status = 'Enter at least one filter.';
    view.statusIsError = true;
    render(el);
    return;
  }
  view.busy = true;
  view.status = 'Searching…';
  view.statusIsError = false;
  render(el);
  try {
    view.results = await searchDiscoveryRepos({ ...view.filters, sort: 'stars', limit: 100 });
    view.resultsSource = 'GitHub search';
    view.selected.clear();
    view.filterText = '';
    view.status = `Found ${view.results.length} repo(s).`;
    view.statusIsError = false;
  } catch (err) {
    view.status = `Search failed: ${err.message}`;
    view.statusIsError = true;
  } finally {
    view.busy = false;
    render(el);
  }
}

/* ── From a list ────────────────────────────────────────────────────────── */

function listPanelHtml() {
  return `
    <p class="mb-s2 max-w-[70ch] text-caveat text-ink-muted">
      A CSV with an <span class="font-mono">address</span> column, or one GitHub URL per line —
      the same shape "Download inventory" writes, so it round-trips. An account URL expands to its
      member repos automatically. Loading fills the same review table as a search below; nothing
      is imported until you select rows and press Import.
    </p>
    <div class="flex flex-wrap items-center gap-s3">
      <label class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-s3 py-[3px] text-caveat text-accent-ink hover:border-accent">
        📄 Load from file
        <input data-list-file type="file" accept=".csv,.txt,text/csv,text/plain" class="hidden">
      </label>
      <button data-act="download-inventory" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-s3 py-[3px] text-caveat text-accent-ink hover:border-accent"
        title="Download every registered resource as CSV — inventory plus current status. Re-importable: the status_ columns are ignored on the way back in.">
        ⬇ Download inventory
      </button>
    </div>
    <textarea data-list-text rows="4" placeholder="https://github.com/org/repo&#10;https://github.com/org/repo2"
      class="mt-s2 w-full rounded-sm border border-rule bg-transparent px-2 py-[4px] font-mono text-caveat text-ink"></textarea>
    <div class="mt-s2 flex items-center gap-s2">
      <button data-act="load-pasted" class="cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[3px] text-caveat text-accent-ink" ${view.busy ? 'disabled' : ''}
        >${view.busy ? 'Loading…' : 'Load pasted list'}</button>
    </div>
    ${statusLineHtml(view.listStatus, view.listStatusIsError)}
  `;
}

function renderListLoadSummary(filename, d) {
  const shown = (d.repos || []).length;
  const lost = (d.rows_read || 0) - shown;
  const parts = [`✓ ${filename} loaded — ${d.rows_read} row(s) read → ${shown} listed below`
    + (d.already_registered ? `, ${d.already_registered} already registered` : '') + '.'];
  if ((d.expanded_orgs || []).length) {
    const bits = d.expanded_orgs.map((o) => o.error
      ? `${o.org} (could not expand: ${o.error})`
      : `${o.org} → ${o.count} repo(s)${o.truncated ? ' (first ' + o.count + ' only)' : ''}`);
    parts.push(`Organisation URL(s) expanded: ${bits.join('; ')}`);
  }
  if ((d.skipped || []).length) {
    parts.push(`${d.skipped.length} row(s) skipped: ${d.skipped.slice(0, 5).join('; ')}`
      + (d.skipped.length > 5 ? ` … and ${d.skipped.length - 5} more` : ''));
  }
  if ((d.unreachable || []).length) {
    parts.push(`${d.unreachable.length} not found on GitHub: ${d.unreachable.slice(0, 5).join(', ')}`
      + (d.unreachable.length > 5 ? ` … and ${d.unreachable.length - 5} more` : ''));
  }
  if (shown) parts.push('Nothing is registered yet — select rows below and Import Selected to add them.');
  view.listStatus = parts.join(' ');
  view.listStatusIsError = lost > 0 && shown === 0;
}

async function loadFromText(el, text, label) {
  view.busy = true;
  view.listStatus = `Reading ${label}…`;
  view.listStatusIsError = false;
  render(el);
  try {
    const d = await discoverFromList(text);
    view.results = d.repos || [];
    view.resultsSource = label;
    view.selected.clear();
    view.filterText = '';
    renderListLoadSummary(label, d);
  } catch (err) {
    view.listStatus = `Could not load ${label}: ${err.message}`;
    view.listStatusIsError = true;
  } finally {
    view.busy = false;
    render(el);
  }
}

async function downloadInventory(el) {
  const btn = el.querySelector('[data-act="download-inventory"]');
  const original = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '⬇ Preparing…'; }
  try {
    const { text, filename } = await fetchInventoryCsv();
    if (!text.trim()) throw new Error('the server returned an empty file');
    const url = URL.createObjectURL(new Blob([text], { type: 'text/csv' }));
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    const rows = Math.max(0, text.trim().split('\n').length - 1);
    view.listStatus = `Downloaded ${filename} — ${rows} resource(s).`;
    view.listStatusIsError = false;
  } catch (err) {
    view.listStatus = `Export failed: ${err.message}. If the server was restarted recently, reload this page.`;
    view.listStatusIsError = true;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = original; }
    render(el);
  }
}

/* ── Results table (shared by both modes) ──────────────────────────────── */

function visibleResults() {
  const term = view.filterText.toLowerCase();
  return view.results
    .map((r, i) => ({ r, i }))
    .filter(({ r }) => !term || [r.full_name, r.description, r.language, r.license]
      .some((v) => (v || '').toLowerCase().includes(term)));
}

/** The Status cell: the persisted import state first, else the registered cue from the search, else the disposition. */
function rowStatusHtml(r) {
  const st = view.importState.get(r.html_url);
  if (st) {
    return `<span data-import-state="${esc(st)}" class="${st === 'failed' ? 'text-state-warn' : 'text-accent-ink'}">${STATE_WORD[st] || esc(st)}</span>`;
  }
  if (r.already_registered) return '<span data-registered class="text-accent-ink">✓ registered</span>';
  return r.disposition && r.disposition !== 'undecided' ? esc(r.disposition) : '';
}

function resultsHtml() {
  if (!view.results.length) return '';
  const visible = visibleResults();
  const groupOptions = view.groups.map((g) =>
    `<option value="${esc(g.slug)}">${esc(g.display_name)}</option>`).join('');
  const selectableCount = visible.filter(({ r }) => !r.already_registered
    && !HIDDEN_DISPOSITIONS.has(r.disposition)).length;

  const rows = visible.map(({ r, i }) => {
    const st = view.importState.get(r.html_url);
    const dimmed = (r.already_registered && st !== 'imported') || HIDDEN_DISPOSITIONS.has(r.disposition);
    const dispTitle = r.disposition_reason
      ? `${r.disposition}${r.disposition_decided_at ? ' — ' + r.disposition_decided_at : ''}: ${r.disposition_reason}`
      : r.disposition;
    return `<tr class="border-b border-rule ${dimmed ? 'opacity-50' : ''}">
      <td class="py-s1 pr-s2"><input type="checkbox" data-row="${i}" ${r.already_registered || st === 'importing' ? 'disabled' : ''} ${view.selected.has(i) ? 'checked' : ''}></td>
      <td class="py-s1 pr-s2 max-w-[220px] truncate font-mono text-caveat text-ink">
        <a href="${esc(r.html_url)}" target="_blank" rel="noopener noreferrer" class="hover:text-accent-ink">${esc(r.full_name)}</a>
      </td>
      <td class="py-s1 pr-s2 max-w-[260px] truncate text-caveat text-ink-muted">${esc(r.description) || '—'}</td>
      <td class="py-s1 pr-s2 text-caveat text-ink-muted">★ ${r.stars}</td>
      <td class="py-s1 pr-s2 text-caveat text-ink-muted">${esc(r.language) || '—'}</td>
      <td class="py-s1 pr-s2 text-caveat text-ink-muted">${esc(r.license) || '—'}</td>
      <td class="py-s1 pr-s2 text-caveat" title="${esc(dispTitle)}">${rowStatusHtml(r)}</td>
      <td class="py-s1 whitespace-nowrap text-caveat">
        ${Object.entries(DISPOSITION_EMOJI).map(([d, glyph]) =>
          `<button data-set-disp="${i}|${d}" title="${esc(d)}" class="cursor-pointer bg-transparent px-[2px] hover:opacity-70">${glyph}</button>`).join('')}
      </td>
    </tr>`;
  }).join('');

  return `
    <div class="mb-s2 flex flex-wrap items-center gap-s2 text-caveat">
      <button data-act="select-all-new" class="cursor-pointer bg-transparent text-accent-ink underline"
        >Select all new${selectableCount ? ` (${selectableCount})` : ''}</button>
      <button data-act="select-none" class="cursor-pointer bg-transparent text-ink-muted underline">Deselect all</button>
      <input data-act="filter" type="text" placeholder="Filter results…" value="${esc(view.filterText)}"
        class="rounded-sm border border-rule bg-transparent px-2 py-[2px] text-caveat text-ink">
      <span class="text-ink-muted">${visible.length} of ${view.results.length} shown${view.resultsSource ? ` · from ${esc(view.resultsSource)}` : ''}</span>
      <select data-act="group" class="ml-auto rounded-sm border border-rule bg-transparent px-2 py-[2px] text-caveat text-ink">
        <option value="">No group</option>${groupOptions}
      </select>
      <button data-act="import-selected" aria-busy="${view.importing ? 'true' : 'false'}" class="${view.importing ? 'cursor-wait opacity-60' : 'cursor-pointer'} rounded-sm border border-accent bg-transparent px-s3 py-[3px] text-caveat text-accent-ink" ${view.busy || view.importing ? 'disabled' : ''}
        >${view.importing ? 'Importing…' : 'Import Selected'}</button>
    </div>
    <div class="max-h-[40vh] overflow-auto rounded-sm border border-rule">
      <table class="w-full text-left">
        <thead class="sticky top-0 bg-paper text-caps uppercase tracking-caps text-ink-muted">
          <tr class="border-b border-rule">
            <th class="px-2 py-s1"></th>
            <th class="px-2 py-s1 font-normal">Name</th>
            <th class="px-2 py-s1 font-normal">Description</th>
            <th class="px-2 py-s1 font-normal">Stars</th>
            <th class="px-2 py-s1 font-normal">Language</th>
            <th class="px-2 py-s1 font-normal">License</th>
            <th class="px-2 py-s1 font-normal">Status</th>
            <th class="px-2 py-s1 font-normal">Disposition</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

async function setRowDisposition(el, i, disposition) {
  const r = view.results[i];
  if (!r) return;
  let reason = '';
  if (HIDDEN_DISPOSITIONS.has(disposition)) {
    const typed = await inPagePrompt({
      title: disposition === 'abandoned' ? 'Abandon' : 'Ignore',
      message: `Why ${disposition === 'abandoned' ? 'abandon' : 'ignore'} ${r.full_name}? (optional)`,
      label: 'Reason (optional)',
      confirmLabel: disposition === 'abandoned' ? 'Abandon' : 'Ignore',
    });
    if (typed === null) return;      // cancelled -- not marked
    reason = typed;
  }
  try {
    await setDisposition(r.html_url, disposition, reason);
    r.disposition = disposition;
    r.disposition_reason = reason;
    render(el);
  } catch (err) {
    view.status = `Failed to set disposition: ${err.message}`;
    view.statusIsError = true;
    render(el);
  }
}

async function importSelected(el) {
  if (view.importing) return;          // a second press while a batch runs does nothing
  const groupSlug = el.querySelector('[data-act="group"]')?.value || '';
  const selected = [...view.selected].map((i) => view.results[i])
    .filter((r) => r && !r.already_registered && view.importState.get(r.html_url) !== 'importing');
  if (!selected.length) {
    view.status = 'No new repos selected.';
    view.statusIsError = true;
    render(el);
    return;
  }
  view.importing = true;
  selected.forEach((r) => view.importState.set(r.html_url, 'importing'));
  render(el);
  let batchId = '';
  try {
    const sourceLabel = view.mode === 'search'
      ? `search: ${view.filters.keyword || 'discover repos'}`
      : `list: ${view.resultsSource || 'from-list'}`;
    const data = await importDiscoveredRepos(selected, { groupSlug, sourceLabel });
    const mark = (urls, state) => (urls || []).forEach((u) => {
      const row = view.results.find((r) => r.html_url === u);
      view.importState.set(u, state);
      if (row && state === 'already_registered') row.already_registered = true;
    });
    mark(data.skipped, 'already_registered');
    // Refused because another batch has them: left as importing, which that batch will settle.
    (data.in_flight || []).forEach((u) => view.importState.set(u, 'importing'));
    view.status = `Importing ${data.queued} repo(s) — watch the Activity log.`
      + (data.skipped.length ? ` ${data.skipped.length} already registered, skipped.` : '')
      + ((data.in_flight || []).length ? ` ${data.in_flight.length} already importing in another batch, left alone.` : '');
    view.statusIsError = false;
    view.selected.clear();
    batchId = data.batch_id || '';
    if (batchId) noticeActivity([{ id: batchId, status: 'running' }]);
  } catch (err) {
    selected.forEach((r) => view.importState.delete(r.html_url));
    view.status = `Failed to start import: ${err.message}`;
    view.statusIsError = true;
  }
  if (!batchId) {
    view.importing = false;
    render(el);
    if (!view.statusIsError) refreshGroupsAndSidebar();
    return;
  }
  render(el);
  await followBatch(el, batchId);
}

/** Re-read the batch's persisted rows until it is done. Every time a repo finishes the sidebar's project list is
 *  re-read (once per tick), and again when the batch ends — a single timed refresh used to fire before most repos
 *  had registered. A failed read is retried on the next tick. */
async function followBatch(el, batchId) {
  const finished = new Set();
  let finalRead = false;
  for (let tick = 0; tick < importPollConfig.maxTicks && !finalRead; tick += 1) {
    await new Promise((r) => setTimeout(r, importPollConfig.intervalMs));
    let st;
    try { st = await getImportStatus(batchId); } catch { continue; }
    let newlyDone = false;
    for (const [url, info] of Object.entries(st.repos || {})) {
      view.importState.set(url, info.state);
      if (info.state === 'imported' || info.state === 'already_registered') {
        const row = view.results.find((r) => r.html_url === url);
        if (row) row.already_registered = true;
      }
      if (info.state !== 'importing' && !finished.has(url)) { finished.add(url); newlyDone = true; }
    }
    finalRead = !!st.done;
    if (finalRead) view.importing = false;
    if (finalRead) view.status = st.summary || view.status;
    if (finalRead) view.statusIsError = st.status === 'error';
    if (el.isConnected) render(el);
    if (newlyDone || finalRead) { try { await refreshGroupsAndSidebar(); } catch { /* the next tick retries */ } }
  }
  if (!finalRead) {                       // gave up following: say so, free the button
    view.importing = false;
    view.status = 'Still importing — see the Activity log for progress.';
    view.statusIsError = false;
    if (el.isConnected) render(el);
  }
}

/* ── Wiring ─────────────────────────────────────────────────────────────── */

function bind(el) {
  el.querySelectorAll('[data-mode]').forEach((b) => b.addEventListener('click', () => {
    view.mode = b.dataset.mode;
    render(el);
  }));

  el.querySelector('[data-act="search"]')?.addEventListener('click', () => runSearch(el));
  el.querySelectorAll('[data-foundation]').forEach((b) => b.addEventListener('click', () => {
    readFiltersFromDom(el);                       // keep what was typed in the other fields
    const fd = (view.foundations || {})[b.dataset.foundation];
    if (!fd) return;
    if (b.getAttribute('aria-pressed') === 'true') {   // pressed again: clear what it filled, search nothing
      view.filters.org = ''; view.filters.topic = '';
      render(el);
      return;
    }
    view.filters.org = fd.org || '';
    view.filters.topic = fd.topic || '';
    // runSearch reads the form back, so the form must hold the foundation's values first.
    ['org', 'topic'].forEach((k) => { const inp = el.querySelector(`[data-f="${k}"]`); if (inp) inp.value = view.filters[k]; });
    runSearch(el);
  }));

  el.querySelector('[data-list-file]')?.addEventListener('change', async (e) => {
    const file = e.target.files && e.target.files[0];
    e.target.value = '';
    if (!file) return;
    const text = await file.text();
    loadFromText(el, text, file.name);
  });
  el.querySelector('[data-act="load-pasted"]')?.addEventListener('click', () => {
    const text = el.querySelector('[data-list-text]')?.value || '';
    if (!text.trim()) {
      view.listStatus = 'Paste at least one GitHub URL first.';
      view.listStatusIsError = true;
      render(el);
      return;
    }
    loadFromText(el, text, 'pasted list');
  });
  el.querySelector('[data-act="download-inventory"]')?.addEventListener('click', () => downloadInventory(el));

  el.querySelectorAll('[data-row]').forEach((cb) => cb.addEventListener('change', () => {
    const i = Number(cb.dataset.row);
    if (cb.checked) view.selected.add(i); else view.selected.delete(i);
  }));
  el.querySelector('[data-act="select-all-new"]')?.addEventListener('click', () => {
    visibleResults().forEach(({ r, i }) => {
      if (!r.already_registered && !HIDDEN_DISPOSITIONS.has(r.disposition)) view.selected.add(i);
    });
    render(el);
  });
  el.querySelector('[data-act="select-none"]')?.addEventListener('click', () => {
    view.selected.clear();
    render(el);
  });
  el.querySelector('[data-act="filter"]')?.addEventListener('input', (e) => {
    view.filterText = e.target.value;
    render(el);
    const again = el.querySelector('[data-act="filter"]');
    if (again) { again.focus(); again.setSelectionRange(again.value.length, again.value.length); }
  });
  el.querySelectorAll('[data-set-disp]').forEach((b) => b.addEventListener('click', () => {
    const [i, disposition] = b.dataset.setDisp.split('|');
    setRowDisposition(el, Number(i), disposition);
  }));
  el.querySelector('[data-act="import-selected"]')?.addEventListener('click', () => importSelected(el));
}
