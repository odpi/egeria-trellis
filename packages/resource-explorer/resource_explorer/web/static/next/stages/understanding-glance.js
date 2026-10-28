/* Understanding — the At a glance headline (pure render functions).
 *
 * Figures come only from reads Understanding already makes: the three "Now" chart responses
 * (`schema_distribution`, `table_sizes`, `column_types`, each carrying its own `state`, `reasons` and `run`) and,
 * for a repository, the diff. Nothing is computed here but sums over fields the server returned and wording.
 * A figure is a cue (the state glyph of the response it came from) plus a short word; the sentence that says how
 * it was derived is its `title`. A figure the server did not establish is "–", never 0.
 */
import { fmtBytes } from '/static/next/stages/native-report.js';

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmtN = (n) => (typeof n === 'number' ? n.toLocaleString('en-US') : String(n));

const GLYPH = { measured: '●', partial: '◐', not_measured: '○' };

const usable = (r) => !!r && !r.__error && r.state !== 'not_measured' && !!r.state;
const sum = (xs) => (xs.some((x) => x === null || x === undefined) ? null : xs.reduce((a, b) => a + b, 0));

/** "10-03 18:07" from a stored survey stamp. */
function stamp(iso) {
  const m = String(iso || '').match(/^\d{4}-(\d{2}-\d{2})[T ](\d{2}:\d{2})/);
  return m ? `${m[1]} ${m[2]}` : String(iso || '');
}

/** Who ran a survey: RE's own, or Egeria's, from the run's stored `source`. Anything else is shown as stored. */
export function surveyedByWords(run) {
  if (!run) return '';
  const src = String(run.source || 'local');
  const as = run.surveyed_as ? `, as ${run.surveyed_as}` : '';
  if (src === 'local') return `RE's own survey${as}`;
  if (src === 'egeria-published') return 'a copy RE published to Egeria (not a measurement)';
  if (/egeria/i.test(src)) return `Egeria's survey${as}`;
  return `${src}${as}`;
}

function fig(name, state, valueHtml, wordHtml, title) {
  return `<span class="inline-flex items-baseline gap-[4px]" data-glance-fig="${name}" data-glance-state="${state}" title="${esc(title || '')}">`
    + `<span class="${state === 'measured' ? 'text-ink-muted' : 'text-state-gap'} font-glyph" aria-hidden="true">${GLYPH[state] || '?'}</span>`
    + `<span class="tnum text-ink"> ${valueHtml}</span> <span class="text-ink-muted">${wordHtml}</span></span>`;
}

const missing = (name, word, why) =>
  fig(name, 'not_measured', '–', word, why || 'No survey measured this.');

/** The database headline: schemas, tables, columns, rows, size, the largest table, and the last survey. */
export function databaseHeadlineHtml({ dist, sizes, types } = {}) {
  const reads = [dist, sizes, types];
  if (reads.every((r) => r && r.__error)) {
    return `<div class="text-caveat text-state-warn" data-glance-headline>The headline figures could not be read: ${esc(dist.__error)}</div>`;
  }
  const out = [];
  const why = (r) => (r && r.__error ? `Could not be read: ${r.__error}` : ((r && r.reasons) || []).join(', ').replace(/_/g, ' ') || 'No survey measured this.');

  // schemas
  out.push(usable(dist)
    ? fig('schemas', dist.state, fmtN(dist.schemas.length), dist.schemas.length === 1 ? 'schema' : 'schemas', dist.scope && dist.scope.partial ? `Within what this credential could read (${dist.scope.fraction || 'part of the database'}).` : '')
    : missing('schemas', 'schemas', why(dist)));

  // tables: the ranked read counts tables without views; the schema read counts everything listed
  const tot = usable(sizes) || (sizes && sizes.totals) ? sizes.totals : null;
  if (tot && typeof tot.tables === 'number') {
    const views = sizes.views_excluded ? ` (${fmtN(sizes.views_excluded)} views not counted)` : '';
    out.push(fig('tables', 'measured', fmtN(tot.tables), tot.tables === 1 ? 'table' : 'tables', `Tables of the latest survey${views}.`));
  } else if (usable(dist)) {
    out.push(fig('tables', dist.state, fmtN(sum(dist.table_counts)), 'tables', 'Tables and views listed per schema.'));
  } else {
    out.push(missing('tables', 'tables', why(dist)));
  }

  // columns
  const cols = usable(dist) ? sum(dist.column_counts) : null;
  if (cols !== null) out.push(fig('columns', 'measured', fmtN(cols), 'columns', ''));
  else if (usable(types)) out.push(fig('columns', types.state, fmtN(types.column_count_total), 'columns', ''));
  else out.push(missing('columns', 'columns', why(types)));

  // rows and size: sums over the tables whose figure the server established; the title says how many
  if (tot && tot.rows !== null && tot.rows !== undefined) {
    const all = tot.rows_tables === tot.tables;
    out.push(fig('rows', all ? 'measured' : 'partial', `${all ? '~' : '≥ ~'}${fmtN(tot.rows)}`, 'rows',
      `Estimated by the server's statistics, summed over ${fmtN(tot.rows_tables)} of ${fmtN(tot.tables)} tables${all ? '' : '; the rest have no established count, so this is a lower bound'}.`));
  } else {
    out.push(missing('rows', 'rows', 'No table has an established row count in this survey.'));
  }
  if (tot && tot.size_bytes !== null && tot.size_bytes !== undefined) {
    const all = tot.size_tables === tot.tables;
    out.push(fig('size', all ? 'measured' : 'partial', `${all ? '' : '≥ '}${esc(fmtBytes(tot.size_bytes))}`, 'size',
      `Relation size on disk, summed over ${fmtN(tot.size_tables)} of ${fmtN(tot.tables)} tables.`));
  } else {
    out.push(missing('size', 'size', 'No table has an established size in this survey.'));
  }

  // largest table (by rows)
  const top = usable(sizes) && (sizes.ranked || [])[0];
  out.push(top
    ? fig('largest', sizes.state, `<span class="font-mono">${esc(top.name)}</span>`,
      `largest · ${fmtN(top.row_count)} rows`, 'The table with the most rows by the server\'s estimate.')
    : missing('largest', 'largest table', why(sizes)));

  // last surveyed, and by whom
  const run = (dist && dist.run) || (sizes && sizes.run) || (types && types.run);
  const last = run
    ? fig('surveyed', 'measured', `<span title="${esc(run.surveyed_at)}">${esc(stamp(run.surveyed_at))}</span>`,
      `surveyed · ${esc(surveyedByWords(run))}`, `Source ${run.source || 'local'}${run.surveyed_as ? `, credential ${run.surveyed_as}` : ''}.`)
    : fig('surveyed', 'not_measured', '–', 'never surveyed', 'No survey has run for this database.');

  return `<div class="mt-s3 flex flex-wrap items-baseline gap-x-s4 gap-y-[2px] text-answer" data-glance-headline>${
    out.join('')}${last}</div>`;
}

/** The repository headline: the file total and the survey date, from the diff Understanding already reads. */
export function repoHeadlineHtml(diff) {
  if (!diff || diff.__error || typeof diff.total_curr !== 'number') return '';
  const when = diff.curr_date ? fig('surveyed', 'measured', esc(stamp(diff.curr_date)), 'surveyed', '') : '';
  return `<div class="mt-s3 flex flex-wrap items-baseline gap-x-s4 gap-y-[2px] text-answer" data-glance-headline>${
    fig('files', 'measured', fmtN(diff.total_curr), diff.total_curr === 1 ? 'file' : 'files', 'Files in the latest survey.')}${when}</div>`;
}
