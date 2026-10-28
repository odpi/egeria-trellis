/* The database report's tabular side (parity slice G3): survey history with its invalid rows,
 * the source word, the three-number line, the ranked tables and the views.
 *
 * Pure functions that return html or small values, so a node test runs them directly. Every word
 * on screen is read from the row or response it describes; where a row cannot say, the screen
 * says "not recorded" or "not measured", never a zero.
 *
 * READ ONLY. The one thing remembered is the "show invalid" toggle, in this browser's
 * localStorage, every access inside try/catch. */
import {
  esc, gridHtml, historyCols, RANKED_COLS, VIEWS_COLS, fmtSize, stamp,
} from '/static/next/stages/report-tables.js';
import { SOURCE_WORD, md } from '/static/next/stages/scope-sources.js';
import { tableCueHtml } from '/static/next/stages/understanding-stats.js';

/* ── PI-030: the source word ────────────────────────────────────────────── */

const SURVEY_SOURCE_WORD = {
  local: 'local survey', custom: 'local survey',
  egeria: 'Egeria', 'egeria-published': 'Egeria',
  hybrid: 'hybrid', 'egeria-custom': 'hybrid',
};

/** "local survey" | "Egeria" | "hybrid" for a survey row's `source`; '' when the row has none;
 *  a code this table does not know shows as itself (an unexplained code is visible). */
export function surveySourceWord(source) {
  const s = String(source || '');
  if (!s) return '';
  return SURVEY_SOURCE_WORD[s] || s;
}

/* ── PI-037: survey history ─────────────────────────────────────────────── */

export const SHOW_INVALID_KEY = 're-next.showInvalidSurveys';

/** The remembered toggle; false when nothing is remembered or storage is unavailable. */
export function readShowInvalid(storage) {
  try { return !!storage && storage.getItem(SHOW_INVALID_KEY) === '1'; } catch { return false; }
}
/** Remember the toggle. Returns whether it was stored; the page works either way. */
export function writeShowInvalid(storage, on) {
  try { storage.setItem(SHOW_INVALID_KEY, on ? '1' : '0'); return true; } catch { return false; }
}

const count = (n) => (typeof n === 'number' ? n.toLocaleString('en-US') : 'not recorded');

/** The history table. `rows` is the `/surveys?include_invalid=true` list, newest first; an invalid
 *  row has `invalid_at`. With `showInvalid` false they are left out and their number is said. */
export function surveyHistoryHtml(rows, showInvalid, { heading = true } = {}) {
  if (!Array.isArray(rows)) {
    return '<div data-survey-history-unreadable class="text-caveat text-state-warn">The survey history could not be read: the server did not send a list.</div>';
  }
  const invalid = rows.filter((r) => r.invalid_at);
  const shown = showInvalid ? rows : rows.filter((r) => !r.invalid_at);
  const toggle = `<label class="inline-flex cursor-pointer items-baseline gap-[4px] text-caveat text-ink">
    <input type="checkbox" data-show-invalid ${showInvalid ? 'checked' : ''}${invalid.length ? '' : ' disabled'}> show invalid${
  invalid.length ? ` (<span class="tnum">${invalid.length}</span>)` : ' (none)'}</label>`;
  const note = !showInvalid && invalid.length
    ? `<span data-invalid-hidden class="text-caveat text-ink-muted">${invalid.length} invalid survey${invalid.length === 1 ? '' : 's'} not shown</span>`
    : '';
  const head = `<div class="mb-s1 flex flex-wrap items-baseline gap-x-s3 gap-y-[2px]">
    ${heading ? '<h3 class="m-0 font-heading text-name font-normal">Survey history</h3>' : ''}${toggle}${note}</div>`;
  if (!shown.length) {
    return `${head}<div data-survey-history-empty class="text-caveat text-ink-muted">No survey has run for this database.</div>`;
  }
  const table = gridHtml(historyCols(showInvalid && invalid.length > 0), 'Survey history', shown.map((r) => {
    const bad = !!r.invalid_at;
    return {
      tone: bad ? 'lowered' : '',
      attrs: `data-survey-row="${esc(r.surveyed_at)}"${bad ? ' data-invalid="1"' : ''}`,
      cells: {
        when: `<span class="tnum" title="${esc(r.surveyed_at)}">${esc(stamp(r.surveyed_at))}</span>`,
        schemas: count(r.schema_count), tables: count(r.table_count), columns: count(r.column_count),
        source: esc(surveySourceWord(r.source) || 'not recorded'),
        invalid: bad
          ? `<span data-invalid-reason>invalid · ${esc(r.invalid_reason || 'no reason recorded')}</span> · marked <span class="tnum" data-invalid-at>${esc(stamp(r.invalid_at))}</span>`
          : '',
      },
    };
  }));
  return `${head}${table}`;
}

/* ── PI-031: the three-number line ──────────────────────────────────────── */

/** "29 schemas · 412 tables · 3,140 columns · from Egeria survey 10-04". The numbers are the
 *  survey the tree was read from (`sources.chosen`), each labelled with what it counts; the column
 *  total comes from the tables' own column counts and says so when some table never had its
 *  columns measured. '' when the tree names no source. */
export function threeNumberLine(tree) {
  const ch = tree && tree.sources && tree.sources.chosen;
  if (!ch) return '';
  const fmt = (n) => Number(n).toLocaleString('en-US');
  const schemas = (tree.schemas || []).filter((s) => s.classification !== 'system');
  let known = 0; let unknown = 0;
  schemas.forEach((s) => (s.tables || []).forEach((t) => {
    const n = typeof t.column_count === 'number' ? t.column_count : ((t.columns || []).length || null);
    if (n == null) unknown += 1; else known += n;
  }));
  const cols = unknown
    ? `${fmt(known)} columns in the tables measured (${fmt(unknown)} tables' columns not measured)`
    : `${fmt(known)} columns`;
  return `${fmt(ch.schemas)} schemas · ${fmt(ch.tables)} tables · ${cols} · from ${SOURCE_WORD[ch.kind] || ch.kind} ${md(ch.as_of)}`;
}

/* ── PI-032: column detail cells ────────────────────────────────────────── */

/** The foreign key target as a short path, or '' when the column has none. */
export function fkTarget(fk) {
  if (!fk || !fk.foreign_table) return '';
  return `${[fk.foreign_schema, fk.foreign_table, fk.foreign_column].filter(Boolean).join('.')}`;
}

/* ── PI-036: ranked tables ──────────────────────────────────────────────── */

/** What the diff the banner reads says about one table: "new", "in both runs", or why it cannot say. */
export function diffWord(diff, name) {
  if (!diff || diff.__error) return diff && diff.__error ? 'diff not readable' : 'no diff yet';
  if (!diff.curr_date) return 'no diff yet';
  if (diff.table_diff_state !== 'measured') return 'not comparable';
  return (diff.new_tables || []).includes(name) ? 'new' : 'in both runs';
}

/** The ranked table under the Tables chart, from the response's `ranked` list. */
export function rankedTablesHtml(resp, diff) {
  const ranked = (resp && resp.ranked) || [];
  if (!ranked.length) return '';
  const rows = ranked.map((r) => ({
    attrs: `data-ranked-row="${esc(r.name)}"`,
    cells: {
      name: `<span class="font-mono">${esc(r.name)}</span>`,
      rows: typeof r.row_count === 'number' ? r.row_count.toLocaleString('en-US') : 'not established',
      size: typeof r.size_bytes === 'number' ? esc(fmtSize(r.size_bytes)) : 'not established',
      analyzed: r.statistics ? tableCueHtml(r.statistics, { withDate: true })
        : (r.last_analyzed ? `<span class="tnum">${esc(stamp(r.last_analyzed))}</span>`
          : (r.activity_state === 'not_collected' ? 'not collected' : 'never analyzed')),
      pending: typeof r.pending_changes === 'number' ? r.pending_changes.toLocaleString('en-US')
        : 'not collected',
      diff: esc(diffWord(diff, r.name)),
    },
  }));
  return `<div data-ranked-tables class="mt-s3"><div class="mb-s1 text-caveat text-ink">Ranked tables</div>${
    gridHtml(RANKED_COLS, 'Ranked tables', rows)}</div>`;
}

/* ── PI-040: views ──────────────────────────────────────────────────────── */

const mq = (s) => String(s ?? '').replace(/["\n\r]/g, ' ');

/** Mermaid source for one view: its columns, the table columns they read from, and the view
 *  between. The words on the diagram say "reads from" and "depends on", never the other word. */
export function viewFlowchartSource(view) {
  let id = 1;
  const view_ = id++;
  const lines = ['flowchart LR'];
  lines.push(`${view_}["View<br/>${mq(view.name)}"]`);
  const cols = Object.keys(view.lineage || {});
  const srcIds = {};
  const leaves = (n) => {
    if (!n) return [];
    const down = n.downstream || [];
    if (!down.length) return n.name && n.source ? [{ source: n.source, name: n.name }] : [];
    return down.flatMap(leaves);
  };
  cols.forEach((c) => {
    const cid = id++;
    lines.push(`${cid}["Column<br/>${mq(c)}"]`);
    lines.push(`${view_} -->|has column| ${cid}`);
    leaves(view.lineage[c]).forEach((leaf) => {
      const key = `${leaf.source}.${leaf.name}`;
      if (!srcIds[key]) {
        srcIds[key] = id++;
        lines.push(`${srcIds[key]}["Table column<br/>${mq(key)}"]`);
        lines.push(`${srcIds[key]} ==>|reads from| ${view_}`);
      }
      lines.push(`${srcIds[key]} -.->|depends on| ${cid}`);
    });
  });
  if (!cols.length) {
    (view.dependencies || []).forEach((d) => {
      const did = id++;
      lines.push(`${did}["Table<br/>${mq(d)}"]`);
      lines.push(`${did} ==>|reads from| ${view_}`);
    });
  }
  return lines.join('\n');
}

/** The Views section. `resp` is the `/views` answer. */
export function viewsSectionHtml(resp, { heading = true } = {}) {
  const head = (extra) => `<div class="flex flex-wrap items-baseline gap-s2">
    ${heading ? '<h3 class="m-0 font-heading text-name font-normal">Views</h3>' : ''}${extra}</div>`;
  if (!resp || resp.__error) {
    return `${head('')}<div data-views-state="error" class="mt-s1 text-answer text-state-warn">Views could not be read: ${esc((resp && resp.__error) || 'no answer')}</div>`;
  }
  if (resp.state === 'never_surveyed') {
    return `${head('')}<div data-views-state="never_surveyed" class="mt-s1 text-answer text-ink">○ not run: needs a database survey</div>`;
  }
  const run = resp.run || {};
  // Which PII keyword source that survey used (the server's one label; absent on older surveys).
  const kw = resp.keyword_source ? ` · <span data-views-keyword-source>${esc(resp.keyword_source)}</span>` : '';
  const from = `<span class="text-caveat text-ink-muted">from the survey of <span class="tnum" title="${esc(run.surveyed_at)}">${esc(stamp(run.surveyed_at))}</span> · ${esc(surveySourceWord(run.source) || 'unknown source')}${kw}</span>`;
  if (resp.state !== 'measured') {
    return `${head(from)}<div data-views-state="not_measured" class="mt-s1 text-answer text-ink">○ not measured — ${esc(resp.reason || 'the server did not say why')}</div>`;
  }
  const views = resp.views || [];
  if (!views.length) {
    return `${head(from)}<div data-views-state="measured" class="mt-s1 text-answer text-ink">This survey looked for views and found none.</div>`;
  }
  const rows = views.map((v, i) => {
    const c = v.complexity || {};
    const hasC = typeof c.complexity_score === 'number';
    const deps = (v.dependencies || []);
    const key = `${v.schema}.${v.name}`;
    return {
      attrs: `data-view="${esc(key)}"`,
      cells: {
        name: `<span class="font-mono">${esc(key)}</span>`,
        complexity: hasC ? `${esc(c.complexity_score)} of 100` : 'not measured',
        portability: typeof c.portability_rating === 'number' ? `${esc(c.portability_rating)}%` : 'not measured',
        joins: hasC ? `${esc(c.join_count ?? 0)} / ${esc(c.cte_count ?? 0)}` : 'not measured',
        depends: deps.length
          ? `<span data-view-deps>${deps.map((d) => `<span class="mr-s1 inline-block font-mono">${esc(d)}</span>`).join('')}</span>`
          : '<span data-view-deps>no table dependency found</span>',
      },
      after: `<div class="ml-s2 mt-[2px] flex flex-col gap-[2px]">
        <details data-view-sql="${i}"><summary class="cursor-pointer text-caveat text-accent-ink underline">SQL</summary>
          <pre class="mt-[2px] max-h-48 overflow-auto whitespace-pre-wrap font-mono text-provenance text-ink">${esc(v.definition || '')}</pre></details>
        <details data-view-flow="${i}"><summary class="cursor-pointer text-caveat text-accent-ink underline">Flowchart</summary>
          <div data-view-flow-body class="mt-[2px] text-caveat text-ink-muted">Open to draw it.</div></details></div>`,
    };
  });
  return `${head(from)}<div data-views-state="measured">${gridHtml(VIEWS_COLS, 'Views', rows)}</div>`;
}
