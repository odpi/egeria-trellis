/* Understanding — the At a glance headline (pure render functions).
 *
 * Figures come only from reads Understanding already makes: the three "Now" chart responses
 * (`schema_distribution`, `table_sizes`, `column_types`, each carrying its own `state`, `reasons` and `run`) and,
 * for a repository, the diff. Nothing is computed here but sums over fields the server returned and wording.
 * A figure is a cue (the state glyph of the response it came from) plus a short word; the sentence that says how
 * it was derived is its `title`. A figure the server did not establish is a state ("? not read" or
 * "○ not measured"), never 0. The headline counts are tiles (ui-cards.js); counts inside prose stay a sentence.
 */
import { fmtBytes } from '/static/next/stages/native-report.js';
import { tileHtml, tileRowHtml } from '/static/next/ui-cards.js';

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

/** The date for a tile's figure and the time for its sub line: "10-19" and "00:30". */
function stampParts(iso) {
  const m = String(iso || '').match(/^\d{4}-(\d{2}-\d{2})[T ](\d{2}:\d{2})/);
  return m ? { date: m[1], time: m[2] } : { date: String(iso || ''), time: '' };
}

/** A size for a tile: the number is the figure and the unit sits small beside it ("5.4" and "MB"). */
function sizeFigure(all, bytes) {
  const s = String(fmtBytes(bytes));
  const i = s.lastIndexOf(' ');
  const num = i > 0 ? s.slice(0, i) : s;
  const unit = i > 0 ? s.slice(i + 1) : '';
  return `${all ? '' : '≥ '}${esc(num)}${unit ? ' ' + `<span data-tile-unit>${esc(unit)}</span>` : ''}`;
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

/** The tile for a figure the server did not establish. A read that FAILED is "? not read" (nothing is known); a read
 *  that answered "not measured" is "○ not measured". Neither draws a number, so neither can be mistaken for 0. */
const absentTile = (key, label, opens, read, why, sub = '') => tileHtml({
  key, label, opens, sub, state: read && read.__error ? 'not_read' : 'not_measured', title: why || 'No survey measured this.',
});

/** A measured count: a real zero is the "∅ 0" state, anything else the figure with its own state. */
const countTile = (key, label, opens, n, state, title = '', sub = '') => tileHtml({
  key, label, opens, sub, title, state: state === 'partial' ? 'partial' : (n === 0 ? 'zero' : 'measured'), figure: fmtN(n),
});

/** The database headline: five tiles (schemas, tables, rows, size, the last survey), each opening what it counted
 *  on the Contents or Over time tab, then the figures that stay in a sentence (columns, the largest table). */
export function databaseHeadlineHtml({ dist, sizes, types } = {}) {
  const reads = [dist, sizes, types];
  if (reads.every((r) => r && r.__error)) {
    return `<div class="text-caveat text-state-warn" data-glance-headline>The headline figures could not be read: ${esc(dist.__error)}</div>`;
  }
  const tiles = [];
  const out = [];
  const why = (r) => (r && r.__error ? `Could not be read: ${r.__error}` : ((r && r.reasons) || []).join(', ').replace(/_/g, ' ') || 'No survey measured this.');

  // schemas
  tiles.push(usable(dist)
    ? countTile('schemas', dist.schemas.length === 1 ? 'schema' : 'schemas', 'schema_distribution', dist.schemas.length, dist.state,
      dist.scope && dist.scope.partial ? `Within what this credential could read (${dist.scope.fraction || 'part of the database'}).` : '')
    : absentTile('schemas', 'schemas', 'schema_distribution', dist, why(dist)));

  // tables: the ranked read counts tables without views; the schema read counts everything listed
  const tot = usable(sizes) || (sizes && sizes.totals) ? sizes.totals : null;
  if (tot && typeof tot.tables === 'number') {
    const views = sizes.views_excluded ? ` (${fmtN(sizes.views_excluded)} views not counted)` : '';
    tiles.push(countTile('tables', tot.tables === 1 ? 'table' : 'tables', 'table_sizes', tot.tables, 'measured', `Tables of the latest survey${views}.`));
  } else if (usable(dist)) {
    tiles.push(countTile('tables', 'tables', 'table_sizes', sum(dist.table_counts), dist.state, 'Tables and views listed per schema.'));
  } else {
    tiles.push(absentTile('tables', 'tables', 'table_sizes', dist, why(dist)));
  }

  // columns (a sentence figure: a count inside prose stays where it is)
  const cols = usable(dist) ? sum(dist.column_counts) : null;
  if (cols !== null) out.push(fig('columns', 'measured', fmtN(cols), 'columns', ''));
  else if (usable(types)) out.push(fig('columns', types.state, fmtN(types.column_count_total), 'columns', ''));
  else out.push(missing('columns', 'columns', why(types)));

  // rows and size: sums over the tables whose figure the server established; the title says how many
  if (tot && tot.rows !== null && tot.rows !== undefined) {
    const all = tot.rows_tables === tot.tables;
    tiles.push(tileHtml({
      key: 'rows', label: 'rows', opens: 'table_sizes', state: all ? 'measured' : 'partial', figure: `${all ? '~' : '≥ ~'}${fmtN(tot.rows)}`,
      title: `Estimated by the server's statistics, summed over ${fmtN(tot.rows_tables)} of ${fmtN(tot.tables)} tables${all ? '' : '; the rest have no established count, so this is a lower bound'}.`,
    }));
  } else {
    tiles.push(tileHtml({ key: 'rows', label: 'rows', opens: 'table_sizes', state: 'not_measured', title: 'No table has an established row count in this survey.' }));
  }
  if (tot && tot.size_bytes !== null && tot.size_bytes !== undefined) {
    const all = tot.size_tables === tot.tables;
    tiles.push(tileHtml({
      key: 'size', label: 'size', opens: 'table_sizes', state: all ? 'measured' : 'partial', figure: sizeFigure(all, tot.size_bytes),
      title: `Relation size on disk, summed over ${fmtN(tot.size_tables)} of ${fmtN(tot.tables)} tables.`,
    }));
  } else {
    tiles.push(tileHtml({ key: 'size', label: 'size', opens: 'table_sizes', state: 'not_measured', title: 'No table has an established size in this survey.' }));
  }

  // largest table (by rows): a sentence figure
  const top = usable(sizes) && (sizes.ranked || [])[0];
  out.push(top
    ? fig('largest', sizes.state, `<span class="font-mono">${esc(top.name)}</span>`,
      `largest · ${fmtN(top.row_count)} rows`, 'The table with the most rows by the server\'s estimate.')
    : missing('largest', 'largest table', why(sizes)));

  // last surveyed, and by whom
  const run = (dist && dist.run) || (sizes && sizes.run) || (types && types.run);
  tiles.push(run
    ? tileHtml({
      key: 'surveyed', label: 'surveyed', opens: 'survey_history', state: 'measured',
      figure: `<span title="${esc(run.surveyed_at)}">${esc(stampParts(run.surveyed_at).date)}</span>`,
      sub: esc([stampParts(run.surveyed_at).time, surveyedByWords(run)].filter(Boolean).join(' · ')),
      title: `Source ${run.source || 'local'}${run.surveyed_as ? `, credential ${run.surveyed_as}` : ''}.`,
    })
    : tileHtml({ key: 'surveyed', label: 'surveyed', opens: 'survey_history', state: 'not_measured', sub: 'never surveyed', title: 'No survey has run for this database.' }));

  return `<div class="mt-s3" data-glance-headline>${tileRowHtml(tiles)}<div class="mt-s3 flex flex-wrap items-baseline gap-x-s4 gap-y-[2px] text-answer">${
    out.join('')}</div></div>`;
}

/** The repository headline: the file total and the survey date, from the diff Understanding already reads. */
export function repoHeadlineHtml(diff) {
  if (!diff || diff.__error || typeof diff.total_curr !== 'number') return '';
  const tiles = [countTile('files', diff.total_curr === 1 ? 'file' : 'files', 'repo-distribution', diff.total_curr, 'measured', 'Files in the latest survey.')];
  if (diff.curr_date) {
    tiles.push(tileHtml({ key: 'surveyed', label: 'surveyed', opens: 'repo-report', state: 'measured', figure: esc(stampParts(diff.curr_date).date), sub: esc(stampParts(diff.curr_date).time) }));
  }
  return `<div class="mt-s3" data-glance-headline>${tileRowHtml(tiles)}</div>`;
}
