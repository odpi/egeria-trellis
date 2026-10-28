/* Analysis.
 *
 * PLAN-FINISH-REPOS.md item 11: Analysis is `built: true` in app.js's
 * `STAGES` array. It needs no bespoke renderer for its Questions checklist
 * -- the generic Questions-checklist engine (`loadPane()` in app.js) already
 * reaches it correctly, the same as Scouting/Enrichment/Curate, because the
 * catalog carries 11 real Analysis-tagged questions (question_catalog.yaml)
 * backed by 11 `intent: analysis` analyses (analysis_catalog.yaml).
 *
 * This module exists for the one thing that DOES need bespoke code: classic's
 * Analysis pane also has a "Sub-Resources" sub-tab (index.html's
 * loadAnalysisSubResourcesView, ~380 lines across candidate discovery,
 * cataloguing and scoped-analysis dispatch). ITEM-11-DISCOVERY-ASSESSMENT-ANALYSIS-IMPLEMENTED.md
 * deferred it by name rather than build or drop it;
 * RULING-SUBRESOURCES-PLACEMENT.md (2026-09-22, docs/design-notes/) then
 * resolved WHERE it belongs, having found it is two features wearing one
 * tab's name:
 *
 *   - the candidate list / selection / catalogue UI is RUN CONFIGURATION --
 *     it belongs on Survey & analyses, attached to sub_resource_survey's own
 *     row, beside the Run button that was otherwise a dead end without it;
 *   - its results are an ORDINARY ANALYSIS'S OUTPUT (`sub_resource_survey`,
 *     intent: analysis, findings-shaped) -- they belong on By analysis, and
 *     in fact already render there with zero code here, because
 *     loadByAnalysisPane()'s dashboard loop already handles any analysis
 *     whose results carry a `findings` array generically (app.js's
 *     `data_profile` board carries sub_resource_survey alongside
 *     data_file_profiling for exactly this reason).
 *
 * Nothing joins the four-tab sub-strip -- the ruling's whole point. This
 * file therefore exports `mountSubResourcePanel`, called from app.js's
 * `renderAnalysesIndexSection` only for the one row whose
 * `analysis_id === 'sub_resource_survey'`, when that row's own
 * "Select & catalog" toggle is opened. The one-line deferral note this
 * module used to export is gone -- the feature it named is now built, and
 * nothing else classic's Sub-Resources view did was left out (see the ruling
 * and this branch's own report for what was and wasn't ported).
 */
import { state, esc, openCurateStage } from '/static/next/app.js';
import {
  getAnalysisResults, listSubResources, listAnalyses, catalogSubResources,
  runScopedAnalysis, getScopedAnalysisResults, isShapeCompatible,
} from '/static/re-api.js';
import {
  createScopeController, choiceCellHtml, egeriaCellHtml, toolbarHtml, manifestLine, SCOPE_HEAD,
} from '/static/next/stages/resource-scope.js';
import { cue, publishLabel, NOTHING_SELECTED_SENTENCE } from '/static/next/stages/repo-manifest.js';

// panel element -> { slug, findings, cataloged, catalog, sortKey, sortDir,
// filterText }. A WeakMap rather than a module-level object because the
// panel itself IS the identity that matters: it is destroyed and rebuilt
// every time the Survey & analyses pane re-renders (a re-run, a resource
// switch), and nothing here should outlive that DOM node.
const PANEL_STATE = new WeakMap();

export async function mountSubResourcePanel(slug, panel) {
  if (!panel) return;
  const cached = PANEL_STATE.get(panel);
  if (cached && cached.slug === slug) {
    renderSubResourcePanel(panel, cached);
    return;
  }
  panel.innerHTML = '<div class="py-s2 text-caveat text-ink-muted">Reading candidates…</div>';
  const me = () => (state.me && (state.me.user_id || state.me.username || state.me.egeria_user)) || '';
  const s = { slug, findings: [], cataloged: {}, catalog: [], sortKey: null, sortDir: 1, filterText: '', publishNote: '' };
  // The same record Curate reads (brief 2a): the panel keeps no selection of its own.
  s.scope = createScopeController({
    slug, me,
    onChange: () => { if (panel.isConnected) renderSubResourcePanel(panel, s); },
    visible: () => subResRows(s),
  });
  try {
    const [results, catalogedRows, catalog] = await Promise.all([
      getAnalysisResults(slug, 'sub_resource_survey').catch(() => ({})),
      listSubResources(slug).catch(() => []),
      // Explicit 'repo': the Sub-resource survey pane is repo-only (sub_resource_survey is a repo analysis).
      listAnalyses('repo').catch(() => []),
    ]);
    s.findings = results.findings || [];
    s.catalog = catalog || [];
    for (const r of catalogedRows || []) s.cataloged[r.locator] = r;
    try { await s.scope.load(); } catch { s.scope.view = null; }
  } catch (err) {
    panel.innerHTML = `<p class="text-caveat text-state-warn">Could not read candidates: ${esc(err.message)}</p>`;
    return;
  }
  if (!panel.isConnected) return; // pane moved on while this awaited
  PANEL_STATE.set(panel, s);
  renderSubResourcePanel(panel, s);
}

function catalogedBadge(row) {
  return row.egeria_guid
    ? '<span class="text-accent-ink" title="Published to Egeria">☁ published</span>'
    : '<span class="text-ink-muted" title="Tracked locally only">🗂 chosen</span>';
}

/** The rows shown (filtered, sorted): the record's rows joined to the survey's own columns by path. */
function subResRows(s) {
  const view = s.scope.view;
  if (!view) return [];
  const byPath = Object.fromEntries(s.findings.map((f) => [f.path || '', f]));
  const t = s.filterText.toLowerCase();
  let rows = view.rows.filter((r) => !t || r.locator.toLowerCase().includes(t)).map((r) => ({ ...r, finding: byPath[r.locator] || {} }));
  if (s.sortKey) {
    const val = (r) => (s.sortKey === 'path' ? r.locator : s.sortKey === 'summary' ? r.reason
      : s.sortKey === 'label' ? r.label : s.sortKey === 'kind' ? r.kind : (r.finding[s.sortKey] ?? ''));
    rows = rows.slice().sort((a, b) => (val(a) < val(b) ? -s.sortDir : val(a) > val(b) ? s.sortDir : 0));
  }
  return rows;
}

function subResRowHtml(r, s) {
  const f = r.finding || {};
  const ownersStr = (f.owners || []).join(', ');
  return `<tr class="border-b border-rule ${r.choice === 'leave_out' || (r.label !== 'worthy' && !r.choice) ? 'opacity-60' : ''}" data-scope-row="${esc(r.locator)}">
    <td class="py-[5px] pr-s2 align-top">${choiceCellHtml(r, s.scope)}</td>
    <td class="py-[5px] pr-s2 max-w-[28ch] truncate font-mono text-caveat text-ink" title="${esc(r.locator)}">${esc(r.locator) || '(root)'}</td>
    <td class="py-[5px] pr-s2 text-caveat text-ink-muted">${r.kind === 'folder' ? '📁' : '📄'} ${esc(r.kind)}</td>
    <td class="py-[5px] pr-s2 text-caveat ${r.label === 'worthy' ? 'text-ink' : 'text-ink-muted'}">${esc(r.label)}</td>
    <td class="py-[5px] pr-s2 max-w-[30ch] truncate text-caveat text-ink-muted" title="${esc(r.reason || '')}">${esc(r.reason || '')}</td>
    <td class="py-[5px] pr-s2 text-caveat text-ink-muted">${esc(f.last_updated_at ? f.last_updated_at.substring(0, 10) : '—')}</td>
    <td class="py-[5px] pr-s2 max-w-[16ch] truncate text-caveat text-ink-muted" title="${esc(ownersStr)}">${esc(ownersStr) || '—'}</td>
    <td class="py-[5px] text-caveat">${egeriaCellHtml(r)}</td>
  </tr>`;
}

const SUBRES_COLS = [
  ['path', 'Path'], ['kind', 'Kind'], ['label', 'Worthy?'],
  ['summary', 'Reason'], ['last_updated_at', 'Last updated'],
];

function subResCatalogedRowHtml(slug, locator, row, catalog) {
  const compatible = catalog.filter((a) => isShapeCompatible(a.target_shape, row.kind));
  const optionsHtml = compatible.length
    ? compatible.map((a) => `<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('')
    : '<option value="">(no scopable analyses)</option>';
  return `<tr class="border-b border-rule" data-subres-catalogued-row data-locator="${esc(locator)}" data-kind="${esc(row.kind)}">
    <td class="py-[5px] pr-s2">${catalogedBadge(row)}</td>
    <td class="py-[5px] pr-s2 max-w-[28ch] truncate font-mono text-caveat text-ink" title="${esc(locator)}">${esc(locator) || '(root)'}</td>
    <td class="py-[5px] pr-s2 text-caveat text-ink-muted">${row.kind === 'folder' ? '📁' : '📄'} ${esc(row.kind)}</td>
    <td class="py-[5px]">
      <div class="flex flex-wrap items-center gap-[6px]">
        <select data-subres-analyze-select ${compatible.length ? '' : 'disabled'}
          class="border border-rule-strong bg-transparent px-1 py-[1px] text-caveat text-ink">${optionsHtml}</select>
        <button type="button" data-subres-run-scoped ${compatible.length ? '' : 'disabled'}
          class="cursor-pointer rounded-sm border border-accent px-2 py-[1px] text-caveat text-accent-ink disabled:cursor-not-allowed disabled:opacity-40"
          >run ›</button>
      </div>
      <div data-subres-scoped-results class="mt-[3px] text-provenance text-ink-muted"></div>
    </td>
  </tr>`;
}

function subResCatalogedSectionHtml(slug, s) {
  const locators = Object.keys(s.cataloged).sort();
  if (!locators.length) return '';
  return `
    <div class="mt-s3 text-caps uppercase tracking-caps text-ink-muted">Chosen files and folders ·
      <span class="tnum">${locators.length}</span></div>
    <p class="mt-[2px] max-w-[70ch] text-provenance text-ink-muted">Run a corpus/container/leaf-shaped analysis
      narrowed to just one of these, instead of the whole repo -- only analyses whose scope fits the item's kind
      are offered.</p>
    <table class="mt-s2 w-full border-collapse text-caveat">
      <thead><tr class="border-b border-rule-strong text-caps uppercase tracking-caps text-ink-muted">
        <th class="py-[4px] pr-s2 text-left"></th>
        <th class="py-[4px] pr-s2 text-left">Locator</th>
        <th class="py-[4px] pr-s2 text-left">Kind</th>
        <th class="py-[4px] text-left">Analyze</th>
      </tr></thead>
      <tbody>${locators.map((loc) => subResCatalogedRowHtml(slug, loc, s.cataloged[loc], s.catalog)).join('')}</tbody>
    </table>`;
}

function renderSubResourcePanel(panel, s) {
  const slug = s.slug;
  const catalogedHtml = subResCatalogedSectionHtml(slug, s);

  const view = s.scope.view;
  if (!view) {
    panel.innerHTML = `<p class="max-w-[70ch] text-caveat text-state-warn">The selection could not be read. Reload to try again.</p>${catalogedHtml}`;
    bindCatalogedSection(panel, slug, s);
    return;
  }
  if (!s.findings.length && !view.rows.length) {
    panel.innerHTML = `
      <p class="max-w-[70ch] text-caveat text-ink-muted">No files-and-folders survey results yet -- use the
        <span class="text-ink">run</span> button above to recommend which folders/files are worth publishing
        as their own Egeria assets, based on the current file inventory. This panel will show the candidate
        list once it has run.</p>
      ${catalogedHtml}`;
    bindCatalogedSection(panel, slug, s);
    return;
  }

  const rows = subResRows(s);
  const m = view.manifest;
  const me = s.scope.me();
  const headerHtml = SUBRES_COLS.map(([key, label]) => `
    <th class="cursor-pointer select-none py-[4px] pr-s2 text-left" data-subres-sort="${key}">${esc(label)}${
      s.sortKey === key ? (s.sortDir === 1 ? ' ▲' : ' ▼') : ''}</th>`).join('');
  const blockers = [];
  if (!me) blockers.push('sign in to publish — the record needs an author');
  if (!m.items) blockers.push(NOTHING_SELECTED_SENTENCE.replace('confirm a line under what it is, or choose', 'choose'));
  else if (!view.in_egeria) blockers.push('the repository is not in Egeria yet');
  const keepScroll = panel.scrollTop;

  panel.innerHTML = `
    <p class="max-w-[70ch] text-caveat text-ink-muted">Choose which folders and files to publish to Egeria as their own assets.
      A choice is saved here, by name, and publishes nothing until you press Publish; selecting a folder never selects what is inside it.
      Repeatable -- come back anytime and add to what's already chosen.</p>

    <div class="mt-s2 flex flex-wrap items-center gap-s2">
      <input type="text" placeholder="Filter by path…" value="${esc(s.filterText)}" data-subres-filter
        class="border border-rule-strong bg-transparent px-[6px] py-[2px] text-caveat text-ink">
      <span class="text-provenance text-ink-muted">${rows.length} of ${view.rows.length} shown</span>
    </div>
    <div class="mt-s2">${toolbarHtml(s.scope, rows)}</div>

    <table class="mt-s2 w-full border-collapse text-caveat">
      <thead><tr class="border-b border-rule-strong text-caps uppercase tracking-caps text-ink-muted">
        <th class="py-[4px] pr-s2 text-left" data-scope-choice-head>${esc(SCOPE_HEAD)}</th>
        ${headerHtml}
        <th class="py-[4px] pr-s2 text-left">Owners</th>
        <th class="py-[4px] text-left">In Egeria</th>
      </tr></thead>
      <tbody>${rows.map((r) => subResRowHtml(r, s)).join('') || `<tr><td colspan="8" class="py-s3 text-center text-ink-muted">No matches.</td></tr>`}</tbody>
    </table>

    <div class="mt-s3 border-t border-rule pt-s2" data-subres-panel-bottom>
      <div data-subres-manifest class="text-caveat text-ink"><span class="tnum">${m.items}</span> item${m.items === 1 ? '' : 's'} to publish ·
        ${esc(manifestLine(m))} · <span class="text-ink-muted">${m.left_out} left out · ${m.proposals_not_accepted} proposals not accepted · ${m.published_earlier} published earlier</span></div>
      <button type="button" data-subres-catalog ${blockers.length ? 'disabled' : ''}
        class="mt-s2 rounded-sm border border-accent px-2 py-[2px] text-caveat text-accent-ink ${blockers.length ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}"
        >${esc(m.items ? `Publish ${m.items} item${m.items === 1 ? '' : 's'}` : 'Publish')}</button>
      ${blockers.map((t) => `<div data-subres-blocker class="mt-[3px] text-caveat text-ink">⚠ ${esc(t)}${
        t.startsWith('the repository') ? ' · <button type="button" data-subres-goto-curate class="cursor-pointer bg-transparent p-0 text-accent-ink underline">publish the repository first →</button>' : ''}</div>`).join('')}
      <div data-subres-feedback class="mt-[3px] text-caveat">${s.publishNote || ''}</div>
    </div>

    ${catalogedHtml}`;
  panel.scrollTop = keepScroll;

  panel.querySelectorAll('[data-subres-sort]').forEach((th) => th.addEventListener('click', () => {
    const key = th.dataset.subresSort;
    if (s.sortKey === key) s.sortDir *= -1; else { s.sortKey = key; s.sortDir = 1; }
    renderSubResourcePanel(panel, s);
  }));
  panel.querySelector('[data-subres-filter]')?.addEventListener('input', (e) => {
    s.filterText = e.target.value;
    renderSubResourcePanel(panel, s);
    const again = panel.querySelector('[data-subres-filter]');
    if (again) { again.focus(); again.setSelectionRange(again.value.length, again.value.length); }
  });
  s.scope.bind(panel);
  panel.querySelector('[data-subres-catalog]')?.addEventListener('click', (e) => submitSubResourceCatalog(panel, slug, e.currentTarget, s));
  panel.querySelector('[data-subres-goto-curate]')?.addEventListener('click', () => openCurateStage());

  bindCatalogedSection(panel, slug, s);
}

/** The press: publishes exactly the record's chosen items (the request names none), then re-reads the record.
 *  States are cues with short words; Egeria's sentence is in the title. */
async function submitSubResourceCatalog(panel, slug, btn, s) {
  if (btn.disabled) return;                                    // a second press while one is out does nothing
  const feedback = panel.querySelector('[data-subres-feedback]');
  btn.disabled = true;
  btn.textContent = 'publishing…';
  if (feedback) feedback.innerHTML = cue('running', 'sent · waiting for Egeria');
  try {
    const data = await catalogSubResources(slug);
    s.publishNote = [
      data.read_back ? cue('measured', `published · ${data.read_back} read back`) : '',
      data.sent ? cue('running', `${data.sent} sent · waiting for Egeria`) : '',
      data.failed ? cue('error', `not published · ${data.failed} not created`) : '',
    ].filter(Boolean).join(' · ') || cue('unrun', 'nothing sent');
    try {
      const rows = await listSubResources(slug);
      s.cataloged = {};
      for (const r of rows || []) s.cataloged[r.locator] = r;
    } catch { /* the cataloged list keeps what it had */ }
    try { await s.scope.load(); } catch { /* the rows keep the state they had */ }
  } catch (err) {
    s.publishNote = cue('error', `not published · ${err.message}`, err.message);
  }
  if (panel.isConnected) renderSubResourcePanel(panel, s);
}

function bindCatalogedSection(panel, slug, s) {
  panel.querySelectorAll('[data-subres-catalogued-row]').forEach((tr) => {
    const locator = tr.dataset.locator;
    const runBtn = tr.querySelector('[data-subres-run-scoped]');
    const select = tr.querySelector('[data-subres-analyze-select]');
    const resultsEl = tr.querySelector('[data-subres-scoped-results]');
    runBtn?.addEventListener('click', async () => {
      const analysisId = select?.value;
      if (!analysisId) return;
      const original = runBtn.textContent;
      runBtn.textContent = '⟳';
      runBtn.disabled = true;
      if (resultsEl) resultsEl.textContent = 'running…';
      try {
        const data = await runScopedAnalysis(slug, analysisId, locator);
        if (data.status === 'ok') {
          await loadScopedResults(slug, analysisId, locator, resultsEl);
        } else if (resultsEl) {
          resultsEl.innerHTML = `<span class="text-state-warn">${esc(data.error || 'failed')}</span>`;
        }
      } catch (err) {
        if (resultsEl) resultsEl.innerHTML = `<span class="text-state-warn">${esc(err.message)}</span>`;
      } finally {
        runBtn.textContent = original;
        runBtn.disabled = false;
      }
    });
  });
}

async function loadScopedResults(slug, analysisId, locator, resultsEl) {
  if (!resultsEl) return;
  try {
    const data = await getScopedAnalysisResults(slug, analysisId, locator);
    if (!Object.keys(data).length) {
      resultsEl.innerHTML = '<span class="text-ink-muted">No results yet.</span>';
      return;
    }
    const parts = Object.entries(data)
      .filter(([k]) => k !== 'surveyed_at')
      .map(([k, v]) => `${esc(k)}: <span class="text-ink">${esc(String(v))}</span>`);
    resultsEl.innerHTML = parts.join(' · ') || '<span class="text-ink-muted">No results yet.</span>';
  } catch (err) {
    resultsEl.innerHTML = `<span class="text-state-warn">Failed to load results: ${esc(err.message)}</span>`;
  }
}
