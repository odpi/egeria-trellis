/* Understanding — charts.
 *
 * Moved out of app.js (PLAN-FINISH-REPOS.md, Part 2 §1): app.js keeps
 * routing, shared state and chrome; each stage owns its own pane's
 * rendering logic. This is Understanding's whole pane — `loadPane()` in
 * app.js calls `loadChartsPane()` directly for `state.stage === 'understanding'`
 * and returns, without going through the generic Questions-checklist engine
 * every other stage shares (see app.js's own comment at that call site).
 *
 * The lower-level chart-rendering machinery (`drawChart`, `chartLayout`,
 * `chartAxes`, `timeAxisData`, `tokens`, `asFigure`, `allPointDates`,
 * `chartIsStale`) stays in app.js rather than moving here, because
 * `drawChart`/`chartLayout`/`tokens` are also used by app.js's own
 * `promoteToPane()` — the chat "promote an answer into the pane" feature,
 * which can promote a chart from ANY stage's chat turn, not just
 * Understanding's. That machinery is shared chrome-adjacent infrastructure,
 * not something this stage owns alone; only the actual per-stage entry
 * point (`loadChartsPane`) is Understanding's to keep changing.
 */
import {
  REPO_CHARTS, getChart, getDbChart, getDatabaseDiff, getDatabaseSurveys, getDatabaseViews,
  getRepoDiff, renderMermaidSvg, getSurveyReport, getAnalysisResults, listAnalyses, getFolderSurvey,
} from '/static/re-api.js';
import { reachLineHtml, folderResultsHtml } from '/static/next/fs-survey.js';
import {
  state, esc, $, paneMessage, bindSubTabs, resourceHeaderHtml, bindResourceHeader,
  asFigure, allPointDates, chartIsStale, drawChart, apiEntityType,
  chartLayout, loadScript, switchUnderstandingTab,
} from '/static/next/app.js';
import { provenanceFromResponse, saveChartImage } from '/static/next/chart-export.js';
import { mountRepoReport } from '/static/next/stages/repo-report.js';
import { mountRepoAnalysisView } from '/static/next/stages/repo-analysis-views.js';
import { fitFigure, annotateTicks, BASE_H } from '/static/next/chart-labels.js';
import { attachGrids, historyCols, RANKED_COLS, VIEWS_COLS } from '/static/next/stages/report-tables.js';
import { statsLineHtml, schemaRollupsHtml, statsOverTimeHtml } from '/static/next/stages/understanding-stats.js';
import { databaseHeadlineHtml, repoHeadlineHtml } from '/static/next/stages/understanding-glance.js';
import { bindTiles } from '/static/next/ui-cards.js';
import {
  surveyHistoryHtml, readShowInvalid, writeShowInvalid, rankedTablesHtml, viewsSectionHtml,
  viewFlowchartSource,
} from '/static/next/stages/db-report.js';

/* ── Understanding for a database ─────────────────────────────────────────
 *
 * REPLY-DESIGNER-CURATE-AND-UNDERSTANDING-ALL-KINDS.md §3-§5. Two sections,
 * "Now" (one run, named in the header) and "Over time". The repo chart kinds
 * and CHART_NO_EQUIVALENT_REASON are gone: a database sees ITS charts.
 *
 * Every status word on screen is read from the response it describes
 * (`state`, `reasons`, `not_established_count`, `run`, `scope`), never from
 * which branch of this file ran. A `not_measured` response draws no figure
 * at all, so a "zero" cannot appear where nothing was measured.
 */
export const FILE_SHARE_SENTENCE =
  'No charts for file systems yet. Their survey isn\'t charted anywhere today.';

//: The chart kinds a database has, in the order a person asks. `route` is
//: the stats route that serves it; a kind with no route is listed and
//: explained ("not wired up yet"), never hidden.
export const DB_CHARTS = [
  { kind: 'schema_distribution', section: 'now', title: 'Tables and columns per schema',
    route: 'schema_distribution', counts: 'tables and columns per schema' },
  { kind: 'table_sizes', section: 'now', title: 'Largest tables, by rows',
    route: 'table_sizes', counts: '' },
  { kind: 'column_types', section: 'now', title: 'Column types',
    route: 'column_types', counts: 'columns per declared type' },
  { kind: 'survey_history', section: 'time', title: 'Structure over runs',
    route: 'survey_history', counts: 'schemas, tables and columns per run, one point per run' },
  { kind: 'table_growth', section: 'time', title: 'Rows per table over runs',
    route: 'table_growth', counts: 'established row counts of the largest tables, per run' },
  { kind: 'table_activity', section: 'time', title: 'Activity per table',
    route: null, counts: '' },
];

const STATE_WORD = { measured: 'measured', partial: 'partial', not_measured: 'not measured' };
const STATE_GLYPH = { measured: '●', partial: '◐', not_measured: '○' };

//: Reason codes the chart routes return, in words. A code not listed shows
//: as itself: an unexplained code is visible, an invented sentence is not.
const REASON_WORDS = {
  credential_scope: 'this credential could read only part of the database',
  row_counts_not_established: 'some row counts are not established',
  sizes_not_established: 'some sizes are not established',
  type_not_recorded: 'some columns have no recorded type',
  runs_without_table_measurement: 'some runs did not measure tables',
  row_counts_missing_in_some_runs: 'some runs have no established row count for some tables',
  never_surveyed: 'no survey has run for this database',
  not_materialized: 'this run predates the structured tables and has not been backfilled',
  not_permitted: 'the credential was not permitted to read this',
  not_collected: 'the server has not collected the statistics for this',
  not_supported: 'this database engine cannot provide this',
  not_measured: 'the step that measures this did not run in that survey',
  no_run_measured_tables: 'no run measured tables',
};
const reasonWord = (r) => REASON_WORDS[r]
  || (/^columns_/.test(r) ? `columns were not measured in this run (${r.slice(8)})` : r);

/** The state line, from the response's own `state` and `reasons`. */
export function stateLineHtml(resp) {
  const st = resp && resp.state;
  const word = STATE_WORD[st];
  if (!word) {
    return `<span data-state="unreported" class="text-state-warn">state not reported by the server</span>`;
  }
  const reasons = Array.isArray(resp.reasons) ? resp.reasons : [];
  return `<span data-state="${esc(st)}" class="${st === 'measured' ? 'text-ink-muted' : 'text-state-gap'}">${
    STATE_GLYPH[st]} ${esc(word)}${reasons.length
    ? ` — ${esc(reasons.map(reasonWord).join('; '))}` : ''}</span>`;
}

const fmtN = (n) => (typeof n === 'number' ? n.toLocaleString('en-US') : String(n));

/** "09-28 14:02" from a run's own timestamp; the full value is the title. */
function runLabel(run) {
  const t = String((run && run.surveyed_at) || '');
  const m = t.match(/^\d{4}-(\d{2}-\d{2})[T ](\d{2}:\d{2})/);
  return m ? `${m[1]} ${m[2]}` : t;
}

function provenanceLineHtml(slug, resp, counts) {
  const run = resp && resp.run;
  const where = run
    ? `run <span class="tnum">${esc(run.surveyed_at)}</span> · source ${esc(run.source || 'unknown')}`
    : 'no run';
  return `${esc(slug)} · ${where}${counts ? ` · ${esc(counts)}` : ''}`;
}

async function plotlyGlobal() {
  if (typeof window !== 'undefined' && window.Plotly) return window.Plotly;
  await loadScript('/static/vendor/plotly.min.js');
  return window.Plotly;
}


/** One chart card. Draws the response's figure unless the response says it
 *  measured nothing. Everything it says comes from `resp`. */
async function drawCard(card, spec, slug, resp, { counts = spec.counts, extraHtml = '' } = {}) {
  const body = card.querySelector('[data-card-body]');
  const provenance = card.querySelector('[data-card-provenance]');
  card.dataset.cardState = (resp && resp.state) || 'unreported';
  card.querySelector('[data-card-state]').innerHTML = stateLineHtml(resp);

  if (!resp || resp.state === 'not_measured') {
    // Not-run family, in the chart's own slot: never "no data", never zeros.
    const reasons = (resp && resp.reasons) || [];
    const neverRan = reasons.includes('never_surveyed');
    body.innerHTML = `<div class="mt-s2 text-answer text-ink" data-not-measured>${
      neverRan
        ? '○ not run: needs a database survey'
        : `○ not measured — ${esc(((resp && resp.notes) || [])[0]
          || reasons.map(reasonWord).join('; ') || 'the server did not say why')}`}${
      neverRan ? `<div class="mt-s2"><button data-run-survey type="button"
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px]
               text-caveat text-accent-ink">Run survey ›</button></div>` : ''}${extraHtml}</div>`;
    const rs = body.querySelector('[data-run-survey]');
    if (rs) {
      rs.addEventListener('click', () => {
        const nav = document.querySelector('#intent-nav button[data-stage="discovery"]');
        if (nav) nav.click();
      });
    }
    provenance.innerHTML = provenanceLineHtml(slug, resp, '');
    return;
  }

  const fig = resp.figure;
  body.innerHTML = `<div data-plot style="height:${BASE_H}px"></div>${extraHtml}`;
  const plot = body.querySelector('[data-plot]');
  provenance.innerHTML = `${provenanceLineHtml(slug, resp, counts)} ·
    <button data-save-image type="button"
      class="cursor-pointer border-0 bg-transparent p-0 text-provenance text-accent-ink underline">Save image</button>
    <span data-save-status class="text-ink-muted"></span>`;
  if (!fig || !Array.isArray(fig.data)) {
    plot.outerHTML = '<div class="text-caveat text-ink-muted" data-no-figure>The server sent no figure for this chart.</div>';
    return;
  }
  const layout = JSON.parse(JSON.stringify(fig.layout || {}));
  delete layout.title;
  const themedFor = (w) => {
    const f = fitFigure({ data: fig.data, layout }, w, spec.title);
    return { fit: f, themed: Object.assign(chartLayout(f.layout), { margin: f.layout.margin }) };
  };
  const widthNow = () => (plot.clientWidth || 600);
  let { fit, themed } = themedFor(widthNow());
  // A horizontal bar chart is as tall as its bars need (a row each); the others keep the base height.
  plot.style.height = `${fit.height}px`;
  try {
    const Plotly = await plotlyGlobal();
    // Not autosized: the fitted margins and tick thinning are for this width,
    // and a resize below refits them.
    await Plotly.newPlot(plot, fig.data, Object.assign(themed, { width: widthNow(), height: fit.height }),
      { displaylogo: false, responsive: false });
    annotateTicks(plot, fit.alt, fit.summary, fit.ticks);
    if (typeof window.addEventListener === 'function' && Plotly.react) {
      let last = widthNow();
      // The card can change width without the window changing (the grid drops to one column), so the plot's own box is
      // watched too, not only the window.
      const refit = () => {
        const w = widthNow();
        if (!plot.isConnected || Math.abs(w - last) < 8) return;
        last = w;
        const again = themedFor(w);
        fit = again.fit;
        plot.style.height = `${fit.height}px`;
        Plotly.react(plot, fig.data, Object.assign(again.themed, { width: w, height: fit.height }),
          { displaylogo: false, responsive: false }).then(() => annotateTicks(plot, fit.alt, fit.summary, fit.ticks));
      };
      window.addEventListener('resize', refit);
      if (typeof ResizeObserver === 'function') new ResizeObserver(refit).observe(plot);
    }
  } catch (err) {
    plot.outerHTML = `<div class="text-answer text-state-warn">${esc(spec.title)} could not be drawn: ${esc(err.message)}</div>`;
  }
  const status = provenance.querySelector('[data-save-status]');
  provenance.querySelector('[data-save-image]').addEventListener('click', async () => {
    status.textContent = '';
    try {
      const Plotly = await plotlyGlobal();
      const title = card.querySelector('[data-card-title]').textContent;
      const out = await saveChartImage({
        Plotly,
        figure: (() => {
          const ex = themedFor(1000).themed;   // refit for the export's own width
          return { data: fig.data, layout: Object.assign({}, ex, {
            title: { text: title }, margin: Object.assign({}, ex.margin, { t: 48 }) }) };
        })(),
        provenance: provenanceFromResponse(slug, title, resp),
      });
      status.textContent = ' saved';
      card.dataset.lastFooter = out.footer;
    } catch (err) {
      status.textContent = ` could not save: ${err.message}`;
    }
  });
}

function cardShell(spec) {
  const card = document.createElement('div');
  card.dataset.chart = spec.kind;
  card.className = 'rounded-sm border border-rule-strong p-s3';
  card.innerHTML = `
    <div class="font-heading text-name" data-card-title>${esc(spec.title)}</div>
    <div class="mt-[2px] text-caveat" data-card-state></div>
    <div data-card-body><div class="mt-s2 text-caveat text-ink-muted">Reading…</div></div>
    <div class="mt-s2 text-provenance text-ink-muted" data-card-provenance></div>`;
  return card;
}

/** Names behind a count ("counts open what they counted"): the count is a
 *  button, the names are a hidden block that sits AFTER the sentence so
 *  opening it never breaks the sentence in two. */
let _namesSeq = 0;
function namesToggle(label, names) {
  if (!names || !names.length) return { label, block: '' };
  const id = `names-${_namesSeq += 1}`;
  return {
    label: `<button type="button" data-names-toggle="${id}" class="cursor-pointer border-0 bg-transparent p-0 text-caveat text-accent-ink underline">${label}</button>`,
    block: `<div hidden data-names="${id}" class="mt-[2px] font-diagram text-provenance">${names.map(esc).join('<br>')}</div>`,
  };
}

/** One delegated handler per host: a names toggle opens its block. */
function bindNamesToggles(root) {
  root.addEventListener('click', (ev) => {
    const t = ev.target.closest && ev.target.closest('[data-names-toggle]');
    if (!t) return;
    const block = root.querySelector(`[data-names="${t.dataset.namesToggle}"]`);
    if (block) block.hidden = !block.hidden;
  });
}

function tableSizesExtra(resp, measure) {
  const what = measure === 'size' ? 'sizes' : 'row counts';
  const other = measure === 'size' ? 'rows' : 'size';
  const bits = [];
  const n = resp.not_established_count || 0;
  if (n) {
    const names = namesToggle(`<span class="tnum">${n}</span> tables' ${what}`, resp.not_established_tables);
    bits.push(`<div class="mt-s2 text-caveat text-ink" data-not-established>? ${names.label
    } not established — statistics not gathered on the server · <button data-measure="${other}" type="button"
      class="cursor-pointer border-0 bg-transparent p-0 text-caveat text-accent-ink underline">${measure === 'size' ? 'by rows instead ›' : 'by size instead ›'}</button>${names.block}</div>`);
  } else if (resp.state === 'not_measured' && measure === 'rows') {
    bits.push(`<div class="mt-s2 text-caveat text-ink"><button data-measure="size" type="button"
      class="cursor-pointer border-0 bg-transparent p-0 text-caveat text-accent-ink underline">by size instead ›</button></div>`);
  }
  if (resp.truncated_by_limit) {
    bits.push(`<div class="mt-[2px] text-caveat text-ink-muted" data-truncated>Showing the largest ${
      fmtN(resp.tables.length)} of ${fmtN(resp.ranked_count)} with an established ${
      measure === 'size' ? 'size' : 'row count'}.</div>`);
  }
  if (resp.views_excluded) {
    bits.push(`<div class="mt-[2px] text-caveat text-ink-muted">${fmtN(resp.views_excluded)} views not counted: a view has no row count to establish.</div>`);
  }
  return bits.join('');
}

function columnTypesExtra(resp) {
  return resp.type_not_recorded
    ? `<div class="mt-s2 text-caveat text-ink" data-type-not-recorded>? type not recorded · <span class="tnum">${fmtN(resp.type_not_recorded)}</span></div>`
    : '';
}

function schemaScopeExtra(resp) {
  const limited = Object.entries(resp.schema_scope || {}).filter(([, v]) => v && v !== 'readable');
  return limited.length
    ? `<div class="mt-s2 text-caveat text-ink-muted">Not fully readable with this credential: ${
      esc(limited.map(([n, v]) => `${n} (${String(v).replace(/_/g, ' ')})`).join(', '))}.</div>`
    : '';
}

/** "Since the last run": a sentence from the existing diff route only. */
export function sinceLastRunHtml(diff) {
  if (!diff || !diff.curr_date) {
    return 'Only one run so far — nothing to compare.';
  }
  const since = String(diff.prev_date || '').slice(0, 10);
  if (diff.table_diff_state !== 'measured') {
    return `Since the run of ${esc(since)}: ${esc(diff.table_diff_note
      || 'the two runs cannot be compared table by table.')}`;
  }
  const added = diff.new_tables || [];
  const removed = diff.removed_tables || [];
  const dCols = (diff.deltas || {}).columns;
  const parts = [];
  const blocks = [];
  const add = (label, names) => { const n = namesToggle(label, names); parts.push(n.label); blocks.push(n.block); };
  if (added.length) add(`${added.length} table${added.length === 1 ? '' : 's'} added`, added);
  if (removed.length) add(`${removed.length} removed`, removed);
  if (typeof dCols === 'number' && dCols !== 0) {
    parts.push(`${Math.abs(dCols)} columns net ${dCols > 0 ? 'added' : 'removed'}`);
  }
  return `Since the run of ${esc(since)}: ${parts.length
    ? parts.join(', ') : 'no tables added or removed and no net change in columns'}.${blocks.join('')}`;
}

/** The one "since the last run" paragraph, for a database and for a repository alike: the same
 *  element, the same classes, the same hook (`data-since-last-run`). Only the sentence differs,
 *  because the two kinds are compared by different measures (tables vs file types). */
export function sinceLastRunBannerHtml(inner) {
  return `<p class="my-s2 text-answer text-ink" data-since-last-run>${inner}</p>`;
}

/** A repository's banner sentence from `/api/egeria/{slug}/diff`: the file total and the file types
 *  that moved. The route keeps the ten largest moves, and the sentence says so when it hit that cap. */
export function repoSinceLastRunHtml(diff) {
  if (!diff || !diff.curr_date) return 'Only one run so far — nothing to compare.';
  const since = String(diff.prev_date || '').slice(0, 10);
  const d = diff.total_delta || 0;
  const total = `${fmtN(diff.total_curr)} files, ${d === 0 ? 'no change in the count' : `${fmtN(Math.abs(d))} ${d > 0 ? 'more' : 'fewer'}`}`;
  const changes = diff.changes || [];
  if (!changes.length) return `Since the run of ${esc(since)}: ${esc(total)} · no file type changed.`;
  const sign = (n) => `${n > 0 ? '+' : '−'}${fmtN(Math.abs(n))}`;
  const label = changes.length >= 10 ? `the ${changes.length} largest file type changes`
    : `${changes.length} file type${changes.length === 1 ? '' : 's'} changed`;
  const names = namesToggle(label, changes.map((c) => `${c.label} ${fmtN(c.prev)} → ${fmtN(c.curr)} (${sign(c.delta)})`));
  return `Since the run of ${esc(since)}: ${esc(total)} · ${names.label}.${names.block}`;
}

/* ── Collapsible sections (project owner, 2026-10-07) ─────────────────────
 * Every section on Understanding folds away behind its own heading. A visible cue (a chevron plus
 * the word "hide" or "show") says which way it will go; the open or closed state is remembered per
 * viewer in this browser only (localStorage, always inside try/catch), and the default is open. */
const COLLAPSE_PREFIX = 're.understanding.collapsed.';
const lsOrNull = () => { try { return window.localStorage; } catch { return null; } };
export function readCollapsed(id, storage = lsOrNull()) {
  try { return !!storage && storage.getItem(COLLAPSE_PREFIX + id) === '1'; } catch { return false; }
}
export function writeCollapsed(id, collapsed, storage = lsOrNull()) {
  try {
    if (!storage) return;
    if (collapsed) storage.setItem(COLLAPSE_PREFIX + id, '1'); else storage.removeItem(COLLAPSE_PREFIX + id);
  } catch { /* the viewer simply loses the memory */ }
}
const cueWords = (open) => (open
  ? '<span class="font-glyph" aria-hidden="true">▾</span> <span data-collapse-word>hide</span>'
  : '<span class="font-glyph" aria-hidden="true">▸</span> <span data-collapse-word>show</span>');

/** A section that can fold. `headingHtml` is the title (an h3 or a plain span), `extraHtml` sits beside it,
 *  `attrs` are extra attributes for the <details> (data hooks, and a literal class="..." so Tailwind sees it), `bodyHtml` is what folds. */
export function collapsibleSectionHtml(id, headingHtml, extraHtml, bodyHtml, { attrs = '' } = {}) {
  const open = !readCollapsed(id);
  return `<details data-collapsible="${esc(id)}" ${attrs} ${open ? 'open' : ''}>
    <summary class="flex cursor-pointer items-baseline gap-s2" title="Fold this section away or open it again">${headingHtml}${extraHtml || ''}
      <span class="ml-auto shrink-0 text-caveat text-ink-muted" data-collapse-cue>${cueWords(open)}</span></summary>
    ${bodyHtml}</details>`;
}

/** Wire every collapsible under `root`: the cue follows the state, and the state is remembered. */
export function bindCollapsibles(root) {
  root.querySelectorAll('details[data-collapsible]').forEach((d) => {
    if (d.dataset.collapseBound) return;
    d.dataset.collapseBound = '1';
    d.addEventListener('toggle', () => {
      const cue = d.querySelector(':scope > summary [data-collapse-cue]');
      if (cue) cue.innerHTML = cueWords(d.open);
      writeCollapsed(d.dataset.collapsible, !d.open);
    });
  });
}

const DB_SECTIONS = [['db-now', 'Now'], ['db-time', 'Over time'], ['db-views', 'Views'], ['db-survey-history', 'Survey history']];

/** An index control was pressed: open the folded section that holds the target (and remember it),
 *  scroll the target into view, and mark the control as the one in use (accent underline, from aria-pressed). */
function gotoTarget(host, control, target) {
  host.querySelectorAll('[data-section-index] button, [data-chart-index] button').forEach((b) =>
    b.setAttribute('aria-pressed', b === control ? 'true' : 'false'));
  if (!target) return;
  // On the tabbed pane the target may sit on a tab that is not showing: open that tab first.
  const panel = target.closest('[data-understanding-tab-panel]');
  if (panel && panel.hidden) switchUnderstandingTab(`understanding_${panel.dataset.understandingTabPanel}`);
  const sec = target.closest('details[data-collapsible]');
  if (sec && !sec.open) {
    sec.open = true;
    const cue = sec.querySelector(':scope > summary [data-collapse-cue]');
    if (cue) cue.innerHTML = cueWords(true);
    writeCollapsed(sec.dataset.collapsible, false);
  }
  if (typeof target.scrollIntoView === 'function') target.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

const h3 = (t) => `<h3 class="m-0 font-heading text-name font-normal">${t}</h3>`;

/** A database's Understanding, mounted into `host` but not yet read. Returns the section markup for each place
 *  it can go and three `start` functions, one per group of reads:
 *    glance    the since-last-run diff
 *    contents  the three "now" charts, the views (and the diff again, from the same memo)
 *    overTime  the two "over time" charts and the survey history
 *  `tabbed: false` is the single-page layout (the sentence inside "Over time", every group read at once);
 *  `tabbed: true` puts the sentence in its own section for At a glance. The sections themselves are the same. */
function databaseParts(slug, { tabbed }) {
  const slot = (c) => `<div data-slot="${c.kind}"></div>`;
  // Tabbed: a chart is never narrower than 32rem (one column below that). The gate-off page stays byte-identical to main.
  const grid = tabbed
    ? 'style="grid-template-columns:repeat(auto-fit,minmax(min(100%,32rem),1fr))"'
    : 'style="grid-template-columns:repeat(auto-fit,minmax(300px,1fr))"';
  const sections = tabbed ? [['db-since', 'Since the last run'], ...DB_SECTIONS] : DB_SECTIONS;
  const indexBar = `
    <div class="mb-s3" data-index-bar>
      <div class="flex flex-wrap items-baseline gap-s2" data-section-index aria-label="Sections on this page">${
  sections.map(([id, label]) => `<button type="button" data-goto="${id}" aria-pressed="false"
        class="wl-chartchip cursor-pointer border-0 bg-transparent px-2 py-[3px] text-caveat text-ink-muted hover:text-ink">${esc(label)}</button>`).join('')}</div>
      <div id="chart-index" class="mt-s2 flex flex-wrap items-baseline gap-s2" data-chart-index aria-label="Charts on this page"></div>
    </div>`;
  // Tabbed layout: the index lives with what it indexes. Contents holds Now and Views, Over time holds the
  // series and the survey history; At a glance has none. The single page keeps its one bar (above).
  const barFor = (id, secs) => `
    <div class="mb-s3" data-index-bar="${id}">
      <div class="flex flex-wrap items-baseline gap-s2" data-section-index aria-label="Sections on this tab">${
  secs.map(([sid, label]) => `<button type="button" data-goto="${sid}" aria-pressed="false"
        class="wl-chartchip cursor-pointer border-0 bg-transparent px-2 py-[3px] text-caveat text-ink-muted hover:text-ink">${esc(label)}</button>`).join('')}</div>
      <div id="chart-index-${id}" class="mt-s2 flex flex-wrap items-baseline gap-s2" data-chart-index="${id}" aria-label="Charts on this tab"></div>
    </div>`;
  const contentsBar = barFor('now', [['db-now', 'Now'], ['db-views', 'Views']]);
  const timeBar = barFor('time', [['db-time', 'Over time'], ['db-survey-history', 'Survey history']]);
  const now = collapsibleSectionHtml('db-now', h3('Now'),
    '<span class="text-caveat text-ink-muted" data-now-header>reading the latest survey…</span>',
    `<div class="mt-s2 grid gap-s3" ${grid}>${DB_CHARTS.filter((c) => c.section === 'now').map(slot).join('')}</div>`,
    { attrs: 'data-section="now"' });
  const time = collapsibleSectionHtml('db-time', h3('Over time'),
    '<span class="text-caveat text-ink-muted" data-time-header></span>',
    `${tabbed ? '' : sinceLastRunBannerHtml('Comparing the last two runs…')}
      <div class="mt-s2 grid gap-s3" ${grid}>${DB_CHARTS.filter((c) => c.section === 'time').map(slot).join('')}</div>`,
    { attrs: tabbed ? 'data-section="time"' : 'data-section="time" class="mt-s6"' });
  const since = collapsibleSectionHtml('db-since', h3('Since the last run'), '',
    sinceLastRunBannerHtml('Comparing the last two runs…'));
  const views = collapsibleSectionHtml('db-views', h3('Views'), '',
    '<div data-views-body><div class="text-caveat text-ink-muted">Reading the views…</div></div>',
    { attrs: 'data-section-views class="mt-s6"' });
  const history = collapsibleSectionHtml('db-survey-history', h3('Survey history'), '',
    '<div data-survey-history-body><div class="text-caveat text-ink-muted">Reading the survey history…</div></div>',
    { attrs: 'data-survey-history class="mt-s6"' });

  // Placement (tabbed): the sentence leads At a glance; the three "now" charts (what is inside, how it is
  // distributed) and the views are Contents; the two series over runs and the survey history are Over time.
  const html = {
    // main's template joined the parts with a newline and four spaces; keep the markup identical
    single: [indexBar, now, time, views, history].join('\n    '),
    indexBar,
    glance: since + '<div class="mt-s3 text-caveat text-ink-muted" data-glance-slot>reading…</div><div class="mt-s3" data-stats-glance></div>',                         // S4b mount 1
    contents: contentsBar + now + '<div data-stats-schemas-slot></div>' + views,                          // S4b mount 2
    overTime: timeBar + time + history + '<div data-stats-time-slot></div>',                          // S4b mount 3
  };

  const mount = (host, { lazy }) => {
    const stale = () => slug !== state.selectedSlug || state.stage !== 'understanding';
    bindNamesToggles(host);
    bindCollapsibles(host);
    const cards = {};
    const chips = {};
    host.querySelectorAll('[data-section-index] button[data-goto]').forEach((b) => {
      b.addEventListener('click', () => gotoTarget(host, b,
        host.querySelector(`details[data-collapsible="${b.dataset.goto}"]`)));
    });
    const chipsFor = (section) => host.querySelector(`[data-chart-index="${section}"]`) || host.querySelector('[data-chart-index]');
    for (const spec of DB_CHARTS) {
      const card = cardShell(spec);
      host.querySelector(`[data-slot="${spec.kind}"]`).replaceWith(card);
      cards[spec.kind] = card;
      // The per-kind list: every chart this database has, each with the state
      // word the server gave it. A kind with no route says so in its entry.
      // A wired chart is a real control (same look as the repo page's index); one with no route
      // is muted text with no border, so it cannot be mistaken for something that responds.
      const wired = !!spec.route;
      const chip = document.createElement(wired ? 'button' : 'span');
      chip.dataset.chartChip = spec.kind;
      if (wired) {
        chip.type = 'button';
        chip.setAttribute('aria-pressed', 'false');
        chip.className = 'wl-chartchip cursor-pointer border-0 bg-transparent px-2 py-[3px] text-caveat text-ink-muted hover:text-ink';
        chip.addEventListener('click', () => gotoTarget(host, chip, card));
      } else {
        chip.className = 'px-2 py-[3px] text-caveat text-ink-muted';
        chip.title = 'Activity per table has no series serving it to this screen yet. It is not a statement about this database.';
      }
      // A chart on a tab that has not opened has not been read: say so, never the "…" of a read in flight.
      chip.textContent = `${spec.title} · ${spec.route ? (lazy ? 'not read yet' : '…') : 'not wired up yet'}`;
      chipsFor(spec.section).appendChild(chip);
      chips[spec.kind] = chip;
    }
    const setChip = (spec, text) => { chips[spec.kind].textContent = `${spec.title} · ${text}`; };
    const specOf = (k) => DB_CHARTS.find((c) => c.kind === k);
    const reading = (section) => DB_CHARTS.filter((c) => c.section === section && c.route).forEach((c) => setChip(c, '…'));

    // Activity per table has no route: present and explained in its own slot.
    const act = cards.table_activity;
    act.querySelector('[data-card-state]').innerHTML =
      '<span data-state="not_wired" class="text-state-gap">○ not wired up yet</span>';
    act.querySelector('[data-card-body]').innerHTML =
      `<div class="mt-s2 text-answer text-ink" data-not-wired>Activity per table needs the
      table-activity series (<span class="font-diagram">db_change_rates</span>), which no
      route serves to this screen yet. It is not a statement about this database.</div>`;
    act.querySelector('[data-card-provenance]').textContent = `${slug} · no run read`;

    const grab = (kind, params) => getDbChart(slug, kind, params)
      .catch((err) => ({ __error: err.message }));
    // The diff feeds the sentence (At a glance) AND the ranked-tables extra (Contents): one read, shared.
    let diffRead = null;
    const readDiff = () => {
      if (!diffRead) diffRead = getDatabaseDiff(slug).catch((err) => ({ __error: err.message }));
      return diffRead;
    };
    const ok = (r) => r && !r.__error;
    // S4b: statistics currency, ONE read shared by the three tabs that show it (each reads only when opened).
    let currencyRead = null;
    const readCurrency = () => {
      if (!currencyRead) currencyRead = grab('statistics_currency');
      return currencyRead;
    };
    // The three "Now" reads feed At a glance (headline) AND Contents (charts): one read each, shared.
    let nowRead = null;
    const readNow = () => {
      if (!nowRead) {
        nowRead = Promise.all([grab('schema_distribution'), grab('table_sizes', { measure: 'rows' }), grab('column_types')]);
      }
      return nowRead;
    };
    const drawStats = async (selector, render) => {
      const slotEl = host.querySelector(selector);
      if (!slotEl) return;
      const resp = await readCurrency();
      if (stale()) return;
      slotEl.innerHTML = render(resp);
    };

    const settle = async (spec, resp, opts) => {
      const card = cards[spec.kind];
      if (resp && resp.__error) {
        card.dataset.cardState = 'error';
        card.querySelector('[data-card-state]').innerHTML =
          '<span data-state="error" class="text-state-warn">could not be read</span>';
        card.querySelector('[data-card-body]').innerHTML =
          `<div class="mt-s2 text-answer text-state-warn">${esc(spec.title)} could not be read: ${esc(resp.__error)}</div>`;
        card.querySelector('[data-card-provenance]').textContent = `${slug} · no run read`;
        setChip(spec, 'could not be read');
        return;
      }
      await drawCard(card, spec, slug, resp, opts);
      setChip(spec, STATE_WORD[resp.state] || 'state not reported');
    };

    const start = {
      async glance() {
        const diff = await readDiff();
        if (stale()) return;
        host.querySelector('[data-since-last-run]').innerHTML = diff && diff.__error
          ? `The comparison with the previous run could not be read: ${esc(diff.__error)}`
          : sinceLastRunHtml(diff);
        const [dist, sizes, types] = await readNow();
        if (stale()) return;
        const headline = host.querySelector('[data-glance-slot]');
        if (headline) {
          headline.innerHTML = databaseHeadlineHtml({ dist, sizes, types });
          // Every tile opens what it counted: the chart card it was read from, or the survey history.
          bindTiles(headline, (target) => gotoTarget(host, null, target === 'survey_history'
            ? host.querySelector('details[data-collapsible="db-survey-history"]') : cards[target]));
        }
        await drawStats('[data-stats-glance]', statsLineHtml);
      },

      async contents() {
        reading('now');
        const [[dist, sizes, types], views, diff] = await Promise.all([
          readNow(),
          getDatabaseViews(slug).catch((err) => ({ __error: err.message })),
          readDiff(),
        ]);
        if (stale()) return;

        await settle(specOf('schema_distribution'), dist, { extraHtml: ok(dist) ? schemaScopeExtra(dist) : '' });
        const drawSizes = async (resp, measure) => {
          await settle(specOf('table_sizes'), resp, {
            counts: ok(resp) ? resp.provenance || '' : '',
            extraHtml: ok(resp) ? tableSizesExtra(resp, measure) + rankedTablesHtml(resp, diff) : '',
          });
          attachGrids(cards.table_sizes, () => RANKED_COLS);
          cards.table_sizes.querySelector('[data-card-title]').textContent =
            measure === 'size' ? 'Largest tables, by size' : 'Largest tables, by rows';
          const sw = cards.table_sizes.querySelector('[data-measure]');
          if (sw) {
            sw.addEventListener('click', async () => {
              const next = sw.dataset.measure;
              const r = await grab('table_sizes', { measure: next });
              if (!stale()) await drawSizes(r, next);
            });
          }
        };
        await drawSizes(sizes, 'rows');
        await settle(specOf('column_types'), types, { extraHtml: ok(types) ? columnTypesExtra(types) : '' });

        // The header comes from the responses' own run / scope fields.
        const withRun = [dist, sizes, types].find((r) => ok(r) && r.run);
        const nowHeader = host.querySelector('[data-now-header]');
        if (withRun) {
          const scope = [dist, sizes, types].map((r) => ok(r) && r.scope).find((sc) => sc && sc.partial);
          nowHeader.innerHTML = `from the survey of <span class="tnum" title="${esc(withRun.run.surveyed_at)}">${
            esc(runLabel(withRun.run))}</span> · ${esc(withRun.run.source || 'unknown source')}${
            scope ? ` · ◐ credential scope: ${esc(scope.schema_fraction || scope.fraction || 'partial')} readable` : ''}`;
        } else {
          nowHeader.textContent = 'no survey has run for this database';
        }
        drawViews(host.querySelector('[data-views-body]'), views);
        await drawStats('[data-stats-schemas-slot]', schemaRollupsHtml);
      },

      async overTime() {
        reading('time');
        const [hist, growth, surveys] = await Promise.all([
          grab('survey_history'), grab('table_growth'),
          getDatabaseSurveys(slug).catch((err) => ({ __error: err.message })),
        ]);
        if (stale()) return;
        await settle(specOf('survey_history'), hist);
        await settle(specOf('table_growth'), growth);
        setChip(specOf('table_activity'), 'not wired up yet');
        const pts = ok(hist) ? hist.points || [] : [];
        host.querySelector('[data-time-header]').textContent = pts.length
          ? `${pts.length} run${pts.length === 1 ? '' : 's'} since ${runLabel(pts[0].run)}` : '';
        drawSurveyHistory(host.querySelector('[data-survey-history-body]'), surveys);
        await drawStats('[data-stats-time-slot]', statsOverTimeHtml);
      },
    };
    return start;
  };
  return { html, mount };
}

/** Understanding on a database, single page (the layout without the leading tabs). Draws into
 *  `#understanding-host`; a missing host throws (the caller writes the message), it never returns silently. */
export async function renderDatabaseUnderstanding(slug) {
  const host = $('understanding-host');
  if (!host) throw new Error('Understanding pane host missing: #understanding-host');
  // Order: Now, Over time, Views, and the survey history LAST. Each folds.
  const parts = databaseParts(slug, { tabbed: false });
  host.innerHTML = parts.html.single;
  const start = parts.mount(host, { lazy: false });
  await Promise.all([start.glance(), start.contents(), start.overTime()]);
}

/** PI-037: the survey history table and its "show invalid" toggle. The list was read once; the
 *  toggle filters it and remembers its state in this browser (never anywhere else). */
function drawSurveyHistory(slot, rows) {
  if (!slot) return;
  const storage = () => { try { return window.localStorage; } catch { return null; } };
  const paint = () => {
    const show = readShowInvalid(storage());
    slot.innerHTML = rows && rows.__error
      ? `<div data-survey-history-unreadable class="text-caveat text-state-warn">The survey history could not be read: ${esc(rows.__error)}</div>`
      : surveyHistoryHtml(rows, show, { heading: false });
    attachGrids(slot, () => historyCols(show && Array.isArray(rows) && rows.some((r) => r.invalid_at)));
    const box = slot.querySelector('[data-show-invalid]');
    if (box) {
      box.addEventListener('change', () => {
        writeShowInvalid(storage(), box.checked);
        paint();
      });
    }
  };
  paint();
}

/** PI-040: the Views section. A flowchart is drawn the first time its disclosure opens and not again;
 *  while it draws, a second open does nothing. */
function drawViews(slot, resp) {
  if (!slot) return;
  slot.innerHTML = viewsSectionHtml(resp, { heading: false });
  attachGrids(slot, () => VIEWS_COLS);
  const views = (resp && resp.views) || [];
  slot.querySelectorAll('[data-view-flow]').forEach((d) => {
    d.addEventListener('toggle', async () => {
      if (!d.open || d.dataset.flowState === 'drawing' || d.dataset.flowState === 'drawn') return;
      const body = d.querySelector('[data-view-flow-body]');
      d.dataset.flowState = 'drawing';
      body.textContent = 'Drawing…';
      try {
        const svg = await renderMermaidSvg(
          `%%{init: {"theme":"neutral","flowchart":{"useMaxWidth":false,"htmlLabels":true}}}%%\n${
            viewFlowchartSource(views[Number(d.dataset.viewFlow)])}`);
        body.innerHTML = svg;
        d.dataset.flowState = 'drawn';
      } catch (err) {
        body.textContent = `The flowchart could not be drawn: ${err.message}`;
        d.dataset.flowState = '';
      }
    });
  });
}

/* ── Understanding for a repository ───────────────────────────────────────
 * Three sections: since the last run, the charts, the survey report. The single-page layout stacks them; the
 * tabbed layout puts the sentence on At a glance, the report on Contents, the charts on Over time. */
function repoParts(slug) {
  const since = collapsibleSectionHtml('repo-since', h3('Since the last run'), '',
    `<div id="repo-diff-banner">${sinceLastRunBannerHtml('Comparing the last two runs…')}</div>`);
  const charts = collapsibleSectionHtml('repo-charts', h3('Charts'), '',
    `<div id="chart-index" class="mt-s2 flex flex-wrap gap-s2"></div>
    <div id="chart-body" class="mt-s4"></div>`, { attrs: 'class="mt-s4"' });
  // Tabbed layout only: the three distribution charts are their own section on Contents (what it is made of,
  // how it is spread); `charts` then holds the series over time. Same chart machinery, own index and body.
  const distribution = collapsibleSectionHtml('repo-distribution', h3('Distribution'), '',
    `<div id="chart-index-distribution" class="mt-s2 flex flex-wrap gap-s2"></div>
    <div id="chart-body-distribution" class="mt-s4"></div>`, { attrs: 'class="mt-s4"' });
  const report = collapsibleSectionHtml('repo-report', h3('Survey report'), '',
    '<div id="repo-report-body"></div>', { attrs: 'class="mt-s4" data-repo-report' });
  // The two results views rebuilt from Classic (owner 2026-10-21): each read the first time its section is open.
  const classification = collapsibleSectionHtml('repo-classification', h3('Classification'), '',
    '<div id="repo-classification-body"></div>', { attrs: 'class="mt-s4" data-repo-analysis="repo_classification"' });
  const depSupport = collapsibleSectionHtml('repo-dependency-support', h3('Dependency support'), '',
    '<div id="repo-dependency-support-body"></div>', { attrs: 'class="mt-s4" data-repo-analysis="dependency_support"' });

  const mount = (root, { canRead = () => true, split = false } = {}) => {
    // PI-053: the full survey report, read when its section is open (and the first time it is opened).
    const reportSection = root.querySelector('details[data-repo-report]');
    let reportRead = false;
    const readReport = () => {
      if (reportRead) return;
      reportRead = true;
      mountRepoReport($('repo-report-body'), slug, {
        fetchReport: getSurveyReport, stale: () => slug !== state.selectedSlug || !reportSection.isConnected,
      });
    };
    reportSection.addEventListener('toggle', () => { if (reportSection.open && canRead()) readReport(); });
    const analysisReads = [];
    root.querySelectorAll('details[data-repo-analysis]').forEach((sec) => {
      const kind = sec.dataset.repoAnalysis;
      let read = false;
      const go = () => {
        if (read) return;
        read = true;
        mountRepoAnalysisView(sec.querySelector('[id$="-body"]'), slug, kind, {
          fetchResults: getAnalysisResults, fetchCatalogue: listAnalyses,
          stale: () => slug !== state.selectedSlug || !sec.isConnected,
        });
      };
      sec.addEventListener('toggle', () => { if (sec.open && canRead()) go(); });
      analysisReads.push(() => { if (sec.open) go(); });
    });

    return {
      async glance() {
        // PI-049: the same banner as a database, read from this repository's own diff route.
        const repoBanner = $('repo-diff-banner');
        bindNamesToggles(repoBanner);
        let inner;
        let diffForHeadline = null;
        try {
          diffForHeadline = await getRepoDiff(slug);
          inner = repoSinceLastRunHtml(diffForHeadline);
        } catch (err) {
          inner = `The comparison with the previous run could not be read: ${esc(err.message)}`;
        }
        if (slug !== state.selectedSlug || !repoBanner.isConnected) return;
        repoBanner.querySelector('[data-since-last-run]').innerHTML = inner;
        if (split && diffForHeadline && !diffForHeadline.__error) {
          repoBanner.insertAdjacentHTML('afterend', repoHeadlineHtml(diffForHeadline));
          const tiles = repoBanner.nextElementSibling;
          if (tiles) bindTiles(tiles, (target) => gotoTarget(root, null, root.querySelector(`details[data-collapsible="${target}"]`)));
        }
      },
      // `split`: the tabbed layout. Contents reads the distribution charts and the report, Over time the rest.
      async contents() {
        if (reportSection.open) readReport();
        analysisReads.forEach((r) => r());
        if (split) await drawRepoCharts(slug, { kinds: REPO_DISTRIBUTION_KINDS, indexId: 'chart-index-distribution', bodyId: 'chart-body-distribution' });
      },
      overTime: () => drawRepoCharts(slug, split ? { kinds: REPO_TIME_KINDS } : {}),
    };
  };
  return { html: { since, charts, distribution, report, classification, depSupport }, mount };
}

const REPO_DISTRIBUTION_KINDS = ['languages', 'file_types', 'top_committers'];
const REPO_TIME_KINDS = REPO_CHARTS.map(([k]) => k).filter((k) => !REPO_DISTRIBUTION_KINDS.includes(k));

async function drawRepoCharts(slug, { kinds = null, indexId = 'chart-index', bodyId = 'chart-body' } = {}) {
  const index = $(indexId);
  const wanted = kinds ? REPO_CHARTS.filter(([k]) => kinds.includes(k)) : REPO_CHARTS;
  index.innerHTML = wanted.map(([kind, label]) =>
    `<button data-chart="${kind}" disabled
      class="cursor-wait rounded-sm border border-rule-strong bg-transparent px-2 py-[3px]
             text-caveat text-ink-muted">${esc(label)}…</button>`).join('');

  // Probe each kind so the index never offers a chart with nothing in it.
  const results = await Promise.all(wanted.map(async ([kind, label]) => {
    try {
      const fig = asFigure(await getChart(slug, kind));
      // POINTS, NOT TRACES. `fig.data.length` counts series, so a trace
      // holding a single observation counted as a usable chart and drew one
      // dot — the flat-line lie in chart form, and the same mistake as a
      // sparkline of one point. Some repos here are at 2.
      const traces = Array.isArray(fig?.data) ? fig.data : [];
      const points = traces.reduce((n, t) => n + (
        (t.x || t.labels || t.r || t.values || []).length), 0);
      const dates = allPointDates(traces);
      return {
        kind, label, fig, traces: traces.length, points,
        first: dates[0] || '', last: dates[dates.length - 1] || '',
        // A date x-axis means the spacing can be honest; a categorical one
        // (languages, file types, committers) has no time to be true to.
        timeAxis: dates.length > 1 && dates.length === points,
        error: null,
      };
    } catch (err) {
      return { kind, label, fig: null, traces: 0, points: 0, last: '', error: err.message };
    }
  }));
  if (slug !== state.selectedSlug) return;
  state.charts = kinds ? [...(state.charts || []).filter((c) => !kinds.includes(c.kind)), ...results] : results;

  index.innerHTML = results.map((r) => {
    if (r.error) {
      return `<span title="${esc(r.error)}"
        class="rounded-sm border border-dashed border-state-warn px-2 py-[3px] text-caveat text-state-warn"
        >${esc(r.label)} · unavailable</span>`;
    }
    if (!r.points) {
      return `<span title="The series exists and has nothing in it yet"
        class="rounded-sm border border-dashed border-rule-strong px-2 py-[3px] text-caveat text-ink-muted"
        >${esc(r.label)} · nothing recorded yet</span>`;
    }
    // ONE OBSERVATION IS NOT A TREND. Offered, because the value is real and
    // worth seeing — labelled, because a chart of it would imply a shape it
    // does not have.
    const one = r.points === 1;
    // SELECTION IS THE ONE THING THAT MUST NEVER BE INFERRED, and it gets the
    // treatment the stage tabs already use — accent ink plus an accent
    // underline — so the app speaks one visual language rather than two.
    // Without it you cannot tell WHICH chart you are looking at, which is what
    // made the unlabelled axes hard to notice underneath.
    return `<button data-chart="${r.kind}" aria-pressed="false"
      title="${r.points} observation(s)${r.last ? ` · latest ${r.last}` : ''}"
      class="wl-chartchip cursor-pointer border-0 bg-transparent px-2 py-[3px]
             text-caveat text-ink-muted hover:text-ink">${esc(r.label)}${
      one ? ' · first measurement'
          : `<span class="tnum text-ink-muted"> · ${r.points}</span>`}${
      r.last && chartIsStale(r.last)
        ? '<span class="wl-age-text"> </span>' : ''}</button>`;
  }).join('');

  const select = (kind) => {
    index.querySelectorAll('[data-chart]').forEach((o) =>
      o.setAttribute('aria-pressed', o.dataset.chart === kind ? 'true' : 'false'));
    drawChart(results.find((r) => r.kind === kind), { bodyId });
  };
  index.querySelectorAll('[data-chart]').forEach((b) =>
    b.addEventListener('click', () => select(b.dataset.chart)));

  const first = results.find((r) => r.points);
  if (first) {
    select(first.kind);
  } else {
    $(bodyId).innerHTML = `<div class="text-answer text-ink">
      Nothing has been recorded for any of this resource's charts yet. That is
      a statement about the history collected so far, not about the resource.</div>`;
  }
}

/** Understanding's leading tabs (At a glance / Contents / Over time) for a repository or a database. Draws the
 *  three panels into `#understanding-tabs-host`, all in the document (the inactive ones `hidden`), and reads
 *  only what the shown tab owns, once. A tab click between them is in place (app.js switchUnderstandingTab):
 *  nothing is read again, and a hidden tab's sections read nothing until it opens.
 *
 *  Placement of the existing sections (nothing inside them changed):
 *    repository  glance: since the last run · contents: distribution charts (languages, file types, top
 *                committers) and the survey report · over time: the other charts
 *    database    glance: since the last run (the sentence that used to sit inside "Over time")
 *                contents: Now (the three distribution charts), Views
 *                over time: Over time (the two series), Survey history */
export async function renderUnderstandingTabs(slug, kind) {
  const host = $('understanding-tabs-host');
  if (!host) throw new Error('Understanding pane host missing: #understanding-tabs-host');
  const stale = () => slug !== state.selectedSlug || state.stage !== 'understanding';
  const started = new Set();
  let start;
  let html;
  let beforePanels = '';
  if (kind === 'database') {
    const parts = databaseParts(slug, { tabbed: true });
    html = { glance: parts.html.glance, contents: parts.html.contents, over_time: parts.html.overTime };
    beforePanels = '';
    host.innerHTML = '';
    host.insertAdjacentHTML('beforeend', beforePanels + panelsHtml(html));
    start = parts.mount(host, { lazy: true });
  } else {
    const parts = repoParts(slug);
    html = { glance: parts.html.since, contents: parts.html.distribution + parts.html.report + parts.html.classification + parts.html.depSupport, over_time: parts.html.charts };
    host.innerHTML = panelsHtml(html);
    bindCollapsibles(host);
    state.charts = [];
    start = parts.mount(host, { canRead: () => started.has('contents'), split: true });
  }
  const run = {
    glance: start.glance, contents: start.contents, over_time: start.overTime,
  };
  const fire = (tab) => {
    if (started.has(tab) || stale()) return Promise.resolve();
    started.add(tab);
    return Promise.resolve(run[tab]()).catch((err) => {
      const panel = host.querySelector(`[data-understanding-tab-panel="${tab}"]`);
      if (panel) panel.insertAdjacentHTML('beforeend', `<p class="max-w-[70ch] text-answer text-state-warn"
        data-understanding-error>Understanding could not be drawn: ${esc(err.message)}</p>`);
    });
  };
  state.understandingTabs = { onTabShown: fire };
  const shown = String(state.subTab || '').replace(/^understanding_/, '');
  await fire(run[shown] ? shown : 'glance');
}

const PANEL_TABS = ['glance', 'contents', 'over_time'];
function panelsHtml(html) {
  const shown = String(state.subTab || '').replace(/^understanding_/, '');
  const active = PANEL_TABS.includes(shown) ? shown : 'glance';
  return PANEL_TABS.map((t) => `<div data-understanding-tab-panel="${t}"${t === active ? '' : ' hidden'}>${html[t]}</div>`).join('');
}

export async function loadChartsPane() {
  const el = $('content');
  if (!el) throw new Error('Understanding pane host missing: #content');
  const slug = state.selectedSlug;

  if (!slug) {
    el.innerHTML = paneMessage('Select a resource',
      'Pick a repository from the sidebar to see its charts.');
    bindSubTabs();
    return;
  }


  // NO sub-tab row here: this is the layout without the leading tabs (a file system, or the tabs switched off
  // in stage-tabs.js). Understanding has no Search/Survey/Dashboard/
  // Questions/Disposition — rendering the strip with "Questions" underlined
  // while a chart is on screen says this pane is something it is not.
  const entityType = apiEntityType(state.resourceType);
  const repo = entityType === 'repo' ? repoParts(slug) : null;
  el.innerHTML = `
    <div class="mb-s4 font-heading text-subtab text-ink">
      <span class="border-b border-accent pb-[2px]">Charts</span>
      <span class="ml-s3 text-caps uppercase tracking-caps text-ink-muted">Understanding has one pane</span>
    </div>
    <div id="resource-header">${resourceHeaderHtml(slug)}</div>
    <div class="my-s3 h-px bg-rule"></div>${repo
    ? `${repo.html.since}
    ${repo.html.charts}
    ${repo.html.report}`
    : '<div id="understanding-host"></div>'}`;
  bindResourceHeader();
  bindCollapsibles(el);
  const repoStart = repo ? repo.mount(el) : null;
  if (repoStart) repoStart.contents();

  // A database has its own six charts (module section above); a file system
  // has none yet and says so. Neither goes through the repo probe.
  if (entityType !== 'repo') {
    if (entityType === 'database') {
      try {
        await renderDatabaseUnderstanding(slug);
      } catch (err) {
        // A renderer that cannot draw says so in the pane, never nothing.
        const host = $('understanding-host') || el;
        host.insertAdjacentHTML('beforeend', `<p class="max-w-[70ch] text-answer text-state-warn"
          data-understanding-error>Understanding could not be drawn: ${esc(err.message)}</p>`);
      }
    } else {
      // Files step 2: a file system's folder survey reads the same whoever led it (Egeria's Folder Survey, or
      // Resource Explorer's walk when only it could read the path), each naming its surveyor. No charts yet.
      $('understanding-host').innerHTML = `<div id="fs-understanding-survey" class="mb-s3"></div>
        <p class="max-w-[70ch] text-answer text-ink" data-understanding-not-charted>${esc(FILE_SHARE_SENTENCE)}</p>`;
      try {
        const view = await getFolderSurvey(slug);
        const host = $('fs-understanding-survey');
        if (host && slug === state.selectedSlug) {
          host.innerHTML = `<div class="text-caps uppercase tracking-caps text-ink-muted">Folder survey</div>
            ${reachLineHtml(view.reach)}${folderResultsHtml(view)}`;
        }
      } catch (err) {
        const host = $('fs-understanding-survey');
        if (host) host.innerHTML = `<p class="text-caveat text-state-warn" data-understanding-error>The folder survey could not be read: ${esc(err.message)}</p>`;
      }
    }
    return;
  }

  repoStart.glance();   // PI-049 (not awaited, as before)
  await repoStart.overTime();
}
