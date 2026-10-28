/* Admin → Repository discovery sources (renamed 2026-10-01: it is GitHub-only
 * and the label must not promise databases until the Find dialog's Saved
 * sources tab takes over; REPLY-DESIGNER-DISCOVERY-SOURCES-ALL-KINDS.md §6) — "where do we scout," as named, reusable
 * configs (SPEC-ADMIN-THE-FOUR-GAPS.md §3).
 *
 * The central point of that spec section: web/routes/discovery.py already
 * has a preview endpoint for refresh (`POST /sources/{slug}/refresh` ->
 * SourceRefreshPreview) and nothing called it. This module makes preview-
 * then-apply the ONLY path for both of this pane's writes that add or
 * remove repos from anything:
 *
 *   - Refresh a fetch_kind-bound list source: preview the added/removed
 *     URL diff, then apply — exactly the existing route pair, just wired up.
 *   - Run a source: the route is READ-ONLY (checked against
 *     run_discovery_source in web/routes/discovery.py — it calls the same
 *     search/list-enrichment helpers as plain /search and returns candidate
 *     DiscoveredRepo rows, no import). So "Run" here shows the candidates,
 *     lets a person choose which ones and which group, and the confirm
 *     before the actual `POST /discovery/import` names the exact count and
 *     destination — the "run imports repositories" behaviour the spec
 *     describes is real, it is just two steps (preview -> import) rather
 *     than one, matching the backend's own shape rather than a guess about
 *     what "run" alone does.
 *
 * Delete's confirmation is worded from what the backend actually does, not
 * a guess: `discovery_sources` (registry.py) is a standalone table with no
 * foreign key into `projects` — deleting a source only removes the saved
 * search/list config. Repos already imported from it live in a completely
 * separate table and are untouched, so the confirmation says so plainly,
 * following this spec's own "name what it does NOT touch" rule (classic's
 * "This does not delete your files on disk" line, §0).
 *
 * Classic had three ways to add a source (_saveGithubSource,
 * _quickAddListSource, _saveCurrentSearchAsSource — index.html's Scouting ->
 * Discover view). `_saveGithubSource` did not create a discovery source at
 * all: it set the GitHub API endpoint override (e.g. for GitHub Enterprise),
 * unrelated config that sat on the same panel. That override is removed
 * (PI-084): `GITHUB_BASE_URL` in `.env` is the single source, so there is
 * nothing to port or to add here.
 * The other two are real and both built below:
 *
 *   - _quickAddListSource -> the "Quick add" tab (one click: create with an
 *     empty URL list + fetch_kind, then refresh-apply to populate it).
 *   - _saveCurrentSearchAsSource -> the "Search" tab. Classic's version
 *     saves filters straight from the Scouting search form; /next has no
 *     equivalent search surface yet (no port of the Scouting -> Discover
 *     view exists under /next/stages), so this builds the admin-panel-side
 *     half: fill filters, Preview against the real search route to see what
 *     it would return (the "cheapest moment to ask" the spec calls out),
 *     then save. Hooking a "save this search" entry point into a future
 *     /next search UI is flagged as a follow-up in
 *     DISCOVERY-SOURCES-ADMIN-IMPLEMENTED.md, not invented here.
 */
import { esc } from '/static/next/app.js';
import { inPageConfirm, inPageNotice } from '/static/next/confirm-dialog.js';
import {
  listDiscoverySources, createDiscoverySource, deleteDiscoverySource,
  runDiscoverySource, previewSourceRefresh, applySourceRefresh,
  searchDiscoveryRepos, listQuickListSources, importDiscoveredRepos, listGroups,
} from '/static/re-api.js';

let _host = null;
let _sources = [];
let _quickList = {};
let _groups = [];

const add = {
  mode: 'search',
  slug: '', displayName: '',
  filters: { org: '', topic: '', language: '', license: '', keyword: '', min_stars: 0 },
  preview: null, previewing: false, previewError: '',
  listUrls: '', fetchKind: '', fetchUrl: '',
  busy: false, message: '',
};

/** The "Run" results panel — at most one open at a time, keyed by slug. */
const run = { slug: '', displayName: '', repos: null, selected: new Set(), groupSlug: '', busy: false, message: '' };

function slugify(s) {
  return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

async function reload() {
  if (!_host) return;
  _host.innerHTML = '<p class="text-answer text-ink-muted">Loading discovery sources…</p>';
  try {
    [_sources, _quickList, _groups] = await Promise.all([
      listDiscoverySources(), listQuickListSources(), listGroups(),
    ]);
  } catch (err) {
    _host.innerHTML = `<p class="max-w-[70ch] text-answer text-state-warn">Could not load discovery sources: ${esc(err.message)}</p>`;
    return;
  }
  render();
}

function configSummary(s) {
  if (s.source_type === 'list') {
    const n = (s.config.urls || []).length;
    return `${n} URL${n === 1 ? '' : 's'}${s.config.fetch_kind ? ` · auto-refresh: ${esc(s.config.fetch_kind)}` : ''}`;
  }
  const parts = ['org', 'topic', 'language', 'license', 'keyword']
    .map((k) => (s.config[k] ? `${k}:${esc(s.config[k])}` : '')).filter(Boolean);
  if (s.config.min_stars) parts.push(`stars>=${s.config.min_stars}`);
  return parts.join(' ') || '(no filters)';
}

function sourcesTableHtml() {
  if (!_sources.length) {
    return '<p class="py-s4 text-center text-caveat text-ink-muted">No discovery sources yet — add one below.</p>';
  }
  const rows = _sources.map((s) => `
    <tr class="border-b border-rule hover:bg-paper-surface">
      <td class="py-s2 pr-s3 text-caveat text-ink">${esc(s.display_name)}</td>
      <td class="py-s2 pr-s3 font-mono text-provenance text-accent-ink">${esc(s.slug)}</td>
      <td class="py-s2 pr-s3 text-caveat text-ink-muted">${s.source_type === 'list' ? '📋 list' : '🔍 search'}</td>
      <td class="max-w-[28ch] truncate py-s2 pr-s3 text-provenance text-ink-muted" title="${esc(configSummary(s))}">${configSummary(s)}</td>
      <td class="whitespace-nowrap py-s2 text-right">
        <button type="button" data-run="${esc(s.slug)}"
          class="mr-1 cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent">▶ Run</button>
        ${s.source_type === 'list' && s.config.fetch_kind
          ? `<button type="button" data-refresh="${esc(s.slug)}"
              class="mr-1 cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent">🔄 Refresh</button>`
          : ''}
        <button type="button" data-delete="${esc(s.slug)}" data-name="${esc(s.display_name)}"
          class="cursor-pointer rounded-sm border border-state-warn bg-transparent px-2 py-[2px] text-caveat text-state-warn">🗑 Delete</button>
      </td>
    </tr>`).join('');
  return `<table class="w-full text-left">
    <thead><tr class="border-b border-rule text-caps uppercase tracking-caps text-ink-muted">
      <th class="pb-s2 pr-s3 font-normal">Name</th>
      <th class="pb-s2 pr-s3 font-normal">Slug</th>
      <th class="pb-s2 pr-s3 font-normal">Type</th>
      <th class="pb-s2 pr-s3 font-normal">Config</th>
      <th class="pb-s2 font-normal"></th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

function addTabBtn(mode, label) {
  const active = add.mode === mode;
  return `<button type="button" data-add-mode="${mode}"
    class="cursor-pointer rounded-sm border px-2 py-[3px] text-caveat
      ${active ? 'border-accent text-accent-ink' : 'border-rule-strong text-ink-muted hover:text-ink'}">${esc(label)}</button>`;
}

function searchFormHtml() {
  const f = add.filters;
  const previewBlock = add.previewing
    ? '<p class="mt-s2 text-caveat text-ink-muted">Searching…</p>'
    : add.previewError
      ? `<p class="mt-s2 text-caveat text-state-warn">${esc(add.previewError)}</p>`
      : add.preview
        ? `<div class="mt-s2 rounded-sm border border-rule bg-paper-surface p-s2">
            <p class="text-caveat text-ink">Found ${add.preview.length} repo${add.preview.length === 1 ? '' : 's'}${
              add.preview.length ? ': ' + esc(add.preview.slice(0, 5).map((r) => r.full_name).join(', ')) + (add.preview.length > 5 ? ', …' : '') : ''
            }</p>
            <p class="mt-[4px] max-w-[60ch] text-provenance text-ink-muted">This is what saving this search would
              re-run every time it's used — the cheapest moment to save it is right after seeing that it's good.</p>
            <div class="mt-s2 flex flex-wrap items-end gap-s2">
              <div><label class="block text-provenance text-ink-muted">Slug</label>
                <input data-add-slug type="text" value="${esc(add.slug)}" placeholder="cncf-data-tools"
                  class="w-40 rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" /></div>
              <div><label class="block text-provenance text-ink-muted">Display name</label>
                <input data-add-name type="text" value="${esc(add.displayName)}" placeholder="CNCF Data Tools"
                  class="w-52 rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" /></div>
              <button type="button" data-save-search
                class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[3px] text-caveat text-accent-ink">💾 Save this search as a source</button>
            </div>
          </div>`
        : '';
  return `
    <div class="grid grid-cols-2 gap-s2 sm:grid-cols-3">
      ${['org', 'topic', 'language', 'license', 'keyword'].map((k) => `
        <div><label class="block text-provenance text-ink-muted">${esc(k)}</label>
          <input data-filter="${k}" type="text" value="${esc(f[k])}"
            class="w-full rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" /></div>
      `).join('')}
      <div><label class="block text-provenance text-ink-muted">min stars</label>
        <input data-filter="min_stars" type="number" min="0" value="${f.min_stars || 0}"
          class="w-full rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" /></div>
    </div>
    <button type="button" data-preview-search
      class="mt-s2 cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] text-caveat text-ink hover:border-accent">🔍 Preview</button>
    ${previewBlock}`;
}

function listFormHtml() {
  const fetchOptions = [['', '— none, manual list only —'], ...Object.entries(_quickList).map(([k, v]) => [k, v.display_name || k])];
  return `
    <div class="flex flex-wrap items-end gap-s2">
      <div><label class="block text-provenance text-ink-muted">Slug</label>
        <input data-add-slug type="text" value="${esc(add.slug)}" placeholder="my-enterprise-repos"
          class="w-40 rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" /></div>
      <div><label class="block text-provenance text-ink-muted">Display name</label>
        <input data-add-name type="text" value="${esc(add.displayName)}" placeholder="My Enterprise Repos"
          class="w-52 rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" /></div>
    </div>
    <div class="mt-s2"><label class="block text-provenance text-ink-muted">GitHub URLs (one per line)</label>
      <textarea data-add-urls rows="4" placeholder="https://github.com/acme/foo"
        class="w-full rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] font-mono text-caveat text-ink">${esc(add.listUrls)}</textarea></div>
    <div class="mt-s2 flex flex-wrap items-end gap-s2">
      <div><label class="block text-provenance text-ink-muted">Auto-refresh from (optional)</label>
        <select data-add-fetch-kind class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink">
          ${fetchOptions.map(([v, label]) => `<option value="${esc(v)}" ${add.fetchKind === v ? 'selected' : ''}>${esc(label)}</option>`).join('')}
        </select></div>
      <div class="flex-1"><label class="block text-provenance text-ink-muted">Fetch URL override (optional)</label>
        <input data-add-fetch-url type="text" value="${esc(add.fetchUrl)}" placeholder="defaults to the source's own canonical URL"
          class="w-full rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" /></div>
    </div>
    <p class="mt-[4px] text-provenance text-ink-muted">A source with an auto-refresh binding can be re-synced later
      from the table above (🔄 Refresh) — it always previews added/removed URLs before saving, never applies silently.</p>
    <button type="button" data-create-list
      class="mt-s2 cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[3px] text-caveat text-accent-ink">+ Create</button>`;
}

function quickAddHtml() {
  const existingFetchKinds = new Set(_sources.map((s) => s.config && s.config.fetch_kind).filter(Boolean));
  const entries = Object.entries(_quickList).filter(([k]) => !existingFetchKinds.has(k));
  if (!entries.length) {
    return '<p class="text-caveat text-ink-muted">Every foundation with a one-click list source is already added — see the table above.</p>';
  }
  return `<p class="mb-s2 max-w-[60ch] text-caveat text-ink-muted">One click creates the source, fetches that
      foundation's own project index right away, and leaves it in the table above — for foundations a GitHub
      search can't cover (many orgs, or a governance/code-org split).</p>
    <div class="flex flex-wrap gap-s2">
      ${entries.map(([k, v]) => `
        <button type="button" data-quick-add="${esc(k)}" data-label="${esc(v.display_name || k)}"
          class="cursor-pointer rounded-sm border border-dashed border-rule-strong px-2 py-[3px] text-caveat text-ink hover:border-accent"
          title="Add and populate a list source from ${esc(v.display_name || k)}'s own project index">📋 + ${esc(v.label || v.display_name || k)}</button>
      `).join('')}
    </div>`;
}

function addFormHtml() {
  return `
    <div class="mt-s4 rounded-sm border border-rule bg-paper-surface p-s3">
      <div class="mb-s2 flex items-center gap-s2">
        <span class="text-caps uppercase tracking-caps text-ink-muted">+ Add a source</span>
        ${addTabBtn('search', '🔍 Search & save')}
        ${addTabBtn('list', '📋 Manual list')}
        ${addTabBtn('quick', '⚡ Quick add')}
      </div>
      ${add.mode === 'search' ? searchFormHtml() : add.mode === 'list' ? listFormHtml() : quickAddHtml()}
      ${add.message ? `<p class="mt-s2 text-caveat ${add.message.startsWith('Failed') ? 'text-state-warn' : 'text-state-ok'}">${esc(add.message)}</p>` : ''}
    </div>`;
}

function runPanelHtml() {
  if (!run.slug) return '';
  if (run.busy && run.repos === null) {
    return `<div class="mt-s4 rounded-sm border border-rule bg-paper-surface p-s3">
      <p class="text-caveat text-ink-muted">Running "${esc(run.displayName)}"…</p></div>`;
  }
  if (run.repos === null) return '';
  const already = run.repos.filter((r) => r.already_registered).length;
  const fresh = run.repos.length - already;
  const groupOptions = ['<option value="">— no group —</option>']
    .concat(_groups.map((g) => `<option value="${esc(g.slug)}">${esc(g.display_name)}</option>`)).join('');
  const rows = run.repos.map((r, i) => `
    <tr class="border-b border-rule ${r.already_registered ? 'opacity-50' : ''}">
      <td class="py-s1 pr-s2"><input type="checkbox" data-run-pick="${i}" ${run.selected.has(i) ? 'checked' : ''} /></td>
      <td class="py-s1 pr-s2 font-mono text-caveat text-ink">${esc(r.full_name)}</td>
      <td class="py-s1 pr-s2 text-provenance text-ink-muted">${r.stars}★</td>
      <td class="py-s1 pr-s2 text-provenance text-ink-muted">${r.already_registered ? 'already registered' : esc(r.disposition || '')}</td>
    </tr>`).join('');
  return `<div class="mt-s4 rounded-sm border border-rule bg-paper-surface p-s3">
    <div class="mb-s2 flex items-center justify-between">
      <span class="text-caveat text-ink">Results for "${esc(run.displayName)}" — ${fresh} new, ${already} already registered</span>
      <button type="button" data-close-run class="cursor-pointer text-provenance text-ink-muted hover:text-ink">✕ close</button>
    </div>
    ${run.repos.length ? `<div class="max-h-[30vh] overflow-auto"><table class="w-full text-left">
      <thead><tr class="text-caps uppercase tracking-caps text-ink-muted"><th class="pb-s1"></th><th class="pb-s1">Repo</th><th class="pb-s1">Stars</th><th class="pb-s1">Status</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      <div class="mt-s2 flex flex-wrap items-center gap-s2">
        <label class="text-caveat text-ink-muted">Import into:
          <select data-run-group class="ml-[4px] rounded-sm border border-rule-strong bg-transparent px-1 py-[2px] text-caveat text-ink">${groupOptions}</select>
        </label>
        <button type="button" data-run-import
          class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[3px] text-caveat text-accent-ink">Import selected</button>
      </div>`
      : '<p class="text-caveat text-ink-muted">No repos found.</p>'}
    ${run.message ? `<p class="mt-s2 text-caveat ${run.message.startsWith('Failed') ? 'text-state-warn' : 'text-state-ok'}">${esc(run.message)}</p>` : ''}
  </div>`;
}

function render() {
  if (!_host) return;
  _host.innerHTML = `
    <h3 class="m-0 font-heading text-name font-normal text-ink">🔍 Repository discovery sources</h3>
    <p class="mt-[2px] max-w-[65ch] text-caveat text-ink-muted">Named, reusable "where do we scout" configs —
      a saved GitHub search, or a curated list of repos (for foundations that spread projects across many orgs,
      like Eclipse, or your own enterprise repos). Running a source only shows candidates for review here — it
      does not register anything until you pick which ones and confirm.</p>
    <p data-github-env-note class="mt-s1 max-w-[65ch] text-caveat text-ink-muted">GitHub's base URL comes from <code class="font-mono">GITHUB_BASE_URL</code> in .env. It is not set here.</p>
    <div class="mt-s3 rounded-sm border border-rule">${sourcesTableHtml()}</div>
    ${addFormHtml()}
    ${runPanelHtml()}`;
  bind();
}

function currentFilters() {
  return { ...add.filters };
}

async function doPreviewSearch() {
  const filters = currentFilters();
  if (!Object.entries(filters).some(([k, v]) => (k === 'min_stars' ? v > 0 : !!v))) {
    add.previewError = 'Fill in at least one filter first.';
    add.preview = null;
    render();
    return;
  }
  add.previewing = true;
  add.previewError = '';
  render();
  try {
    add.preview = await searchDiscoveryRepos(filters);
  } catch (err) {
    add.previewError = `Search failed: ${err.message}`;
    add.preview = null;
  } finally {
    add.previewing = false;
    render();
  }
}

async function doSaveSearch() {
  if (!add.slug || !add.displayName) {
    add.message = 'Failed: slug and display name are both required.';
    render();
    return;
  }
  add.message = 'Saving…';
  render();
  try {
    await createDiscoverySource(add.slug, add.displayName, 'search', currentFilters());
    add.message = '';
    add.slug = ''; add.displayName = ''; add.preview = null;
    await reload();
  } catch (err) {
    add.message = `Failed: ${err.message}`;
    render();
  }
}

async function doCreateList() {
  const urls = add.listUrls.split('\n').map((u) => u.trim()).filter(Boolean);
  if (!add.slug || !add.displayName) {
    add.message = 'Failed: slug and display name are both required.';
    render();
    return;
  }
  if (!urls.length && !add.fetchKind) {
    add.message = "Failed: a list source needs at least one URL, unless it has an auto-refresh source set.";
    render();
    return;
  }
  add.message = 'Creating…';
  render();
  try {
    await createDiscoverySource(add.slug, add.displayName, 'list', {
      urls, fetch_kind: add.fetchKind, fetch_url: add.fetchUrl,
    });
    add.message = '';
    add.slug = ''; add.displayName = ''; add.listUrls = ''; add.fetchKind = ''; add.fetchUrl = '';
    await reload();
  } catch (err) {
    add.message = `Failed: ${err.message}`;
    render();
  }
}

async function doQuickAdd(fetchKind, label) {
  const displayName = label || fetchKind;
  const slug = slugify(displayName);
  if (!(await inPageConfirm({ message: `Add "${displayName}"? This creates a discovery source and immediately fetches its `
    + 'current project list from its own index — no import happens yet, this only populates the saved list.', confirmLabel: 'Confirm' }))) return;
  add.message = `Adding ${displayName}…`;
  render();
  try {
    await createDiscoverySource(slug, displayName, 'list', { fetch_kind: fetchKind });
    await applySourceRefresh(slug);
    add.message = '';
    await reload();
  } catch (err) {
    add.message = `Failed to add ${displayName}: ${err.message}`;
    render();
  }
}

async function doRun(slug, displayName) {
  run.slug = slug;
  run.displayName = displayName;
  run.repos = null;
  run.selected = new Set();
  run.groupSlug = '';
  run.message = '';
  run.busy = true;
  render();
  try {
    const repos = await runDiscoverySource(slug);
    run.repos = repos;
    // Pre-select everything not already registered — mirrors Scouting's own
    // default (already_registered rows shown but dimmed, not auto-imported).
    run.selected = new Set(repos.map((r, i) => i).filter((i) => !repos[i].already_registered));
  } catch (err) {
    run.repos = [];
    run.message = `Failed: ${err.message}`;
  } finally {
    run.busy = false;
    render();
  }
}

async function doImportSelected() {
  const picked = run.repos.filter((_, i) => run.selected.has(i));
  if (!picked.length) {
    run.message = 'Failed: nothing selected.';
    render();
    return;
  }
  const group = _groups.find((g) => g.slug === run.groupSlug);
  const dest = group ? `group "${group.display_name}"` : 'no group';
  if (!(await inPageConfirm({ message: `Import ${picked.length} repo${picked.length === 1 ? '' : 's'} into ${dest}?`, confirmLabel: 'Confirm' }))) return;
  run.busy = true;
  run.message = 'Queuing import…';
  render();
  try {
    const res = await importDiscoveredRepos(picked, { groupSlug: run.groupSlug, sourceLabel: `discovery source: ${run.displayName}` });
    run.message = `Queued ${res.queued} for import${res.skipped.length ? ` (${res.skipped.length} already registered, skipped)` : ''}. See Activity for progress.`;
  } catch (err) {
    run.message = `Failed: ${err.message}`;
  } finally {
    run.busy = false;
    render();
  }
}

async function doRefresh(slug) {
  let preview;
  try {
    preview = await previewSourceRefresh(slug);
  } catch (err) {
    await inPageNotice({ message: `Could not preview refresh: ${err.message}` });
    return;
  }
  if (!preview.added.length && !preview.removed.length) {
    await inPageNotice({ title: 'Nothing to do', message: `Already up to date (${preview.fetched_count} URLs).` });
    return;
  }
  const msg = `Fetched ${preview.fetched_count} URL(s) (currently ${preview.current_count} saved):\n`
    + `+ ${preview.added.length} added\n- ${preview.removed.length} removed\n\nApply these changes?`;
  if (!(await inPageConfirm({ message: msg, confirmLabel: 'Confirm' }))) return;
  try {
    await applySourceRefresh(slug);
  } catch (err) {
    await inPageNotice({ message: `Refresh failed: ${err.message}` });
    return;
  }
  await reload();
}

async function doDelete(slug, name) {
  if (!(await inPageConfirm({ message: `Remove discovery source "${name}"? This only deletes the saved search/list config — `
    + 'it does not affect any repositories already imported from it (they live in the registry independently '
    + 'of the source that found them).', confirmLabel: 'Confirm' }))) return;
  try {
    await deleteDiscoverySource(slug);
    if (run.slug === slug) { run.slug = ''; run.repos = null; }
    await reload();
  } catch (err) {
    await inPageNotice({ message: `Failed to delete: ${err.message}` });
  }
}

function bind() {
  if (!_host) return;
  _host.querySelectorAll('[data-run]').forEach((b) => b.addEventListener('click', () => {
    const s = _sources.find((x) => x.slug === b.dataset.run);
    doRun(b.dataset.run, s ? s.display_name : b.dataset.run);
  }));
  _host.querySelectorAll('[data-refresh]').forEach((b) => b.addEventListener('click', () => doRefresh(b.dataset.refresh)));
  _host.querySelectorAll('[data-delete]').forEach((b) => b.addEventListener('click', () => doDelete(b.dataset.delete, b.dataset.name)));

  _host.querySelectorAll('[data-add-mode]').forEach((b) => b.addEventListener('click', () => {
    add.mode = b.dataset.addMode;
    add.message = '';
    render();
  }));

  _host.querySelectorAll('[data-filter]').forEach((inp) => inp.addEventListener('change', () => {
    const key = inp.dataset.filter;
    add.filters[key] = key === 'min_stars' ? (parseInt(inp.value, 10) || 0) : inp.value;
  }));
  const previewBtn = _host.querySelector('[data-preview-search]');
  if (previewBtn) previewBtn.addEventListener('click', doPreviewSearch);
  const slugInput = _host.querySelector('[data-add-slug]');
  if (slugInput) slugInput.addEventListener('change', () => { add.slug = slugInput.value.trim(); });
  const nameInput = _host.querySelector('[data-add-name]');
  if (nameInput) nameInput.addEventListener('change', () => { add.displayName = nameInput.value.trim(); });
  const saveSearchBtn = _host.querySelector('[data-save-search]');
  if (saveSearchBtn) saveSearchBtn.addEventListener('click', doSaveSearch);

  const urlsInput = _host.querySelector('[data-add-urls]');
  if (urlsInput) urlsInput.addEventListener('change', () => { add.listUrls = urlsInput.value; });
  const fetchKindSel = _host.querySelector('[data-add-fetch-kind]');
  if (fetchKindSel) fetchKindSel.addEventListener('change', () => { add.fetchKind = fetchKindSel.value; });
  const fetchUrlInput = _host.querySelector('[data-add-fetch-url]');
  if (fetchUrlInput) fetchUrlInput.addEventListener('change', () => { add.fetchUrl = fetchUrlInput.value.trim(); });
  const createListBtn = _host.querySelector('[data-create-list]');
  if (createListBtn) createListBtn.addEventListener('click', doCreateList);

  _host.querySelectorAll('[data-quick-add]').forEach((b) => b.addEventListener('click', () => doQuickAdd(b.dataset.quickAdd, b.dataset.label)));

  const closeRunBtn = _host.querySelector('[data-close-run]');
  if (closeRunBtn) closeRunBtn.addEventListener('click', () => { run.slug = ''; run.repos = null; render(); });
  _host.querySelectorAll('[data-run-pick]').forEach((cb) => cb.addEventListener('change', () => {
    const i = parseInt(cb.dataset.runPick, 10);
    if (cb.checked) run.selected.add(i); else run.selected.delete(i);
  }));
  const groupSel = _host.querySelector('[data-run-group]');
  if (groupSel) groupSel.addEventListener('change', () => { run.groupSlug = groupSel.value; });
  const importBtn = _host.querySelector('[data-run-import]');
  if (importBtn) importBtn.addEventListener('click', doImportSelected);
}

export async function renderDiscoverySources(host) {
  _host = host;
  add.mode = 'search';
  add.message = '';
  add.preview = null;
  run.slug = '';
  run.repos = null;
  await reload();
}
