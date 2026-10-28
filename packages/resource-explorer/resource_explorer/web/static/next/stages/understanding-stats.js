/* Understanding — statistics currency (S4b). Pure render functions only.
 *
 * Every number and state here arrives from the server's one derivation
 * (`resource_explorer/statistics_currency.py`, served by
 * `/api/stats/databases/{slug}/statistics_currency` and, per ranked table, as
 * `statistics` on `table_sizes`). Nothing is computed here but wording: no
 * threshold, no date arithmetic, no clock. A state is a glyph plus a short
 * word; the threshold and the as-of rule are in `title`, not in the line.
 *
 * No imports: the only helper needed is escaping.
 */
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const OK = 'ok';
const WARN = 'warn';
const MUTED = 'muted';

/** "2026-10-02T..." -> "10-02". */
export function monthDay(iso) {
  const m = /^\d{4}-(\d{2}-\d{2})/.exec(String(iso || ''));
  return m ? m[1] : '';
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const span = (tone, glyph, word, attrs = '') =>
  `<span class="${tone === OK ? 'text-state-ok' : tone === WARN ? 'text-state-warn' : 'text-ink-muted'}" ${attrs}><span class="font-glyph" aria-hidden="true">${glyph}</span> ${esc(word)}</span>`;

/** A date-and-age phrase for the per-table cell: "10-08 (3d before survey)". */
function datedAge(st) {
  const d = monthDay(st.last_analyzed);
  return d && typeof st.age_days === 'number' ? `${d} (${st.age_days}d before survey)` : '';
}

/** One table's cue from the server's `statistics` object. `withDate` adds the analyze date next to the cue
 *  (the report view used to show the timestamp). The title always names the survey the age is measured at. */
export function tableCueHtml(st, { withDate = false } = {}) {
  if (!st || st.state == null) return span(MUTED, '?', 'not collected', 'data-stats-state="not_collected"');
  const age = typeof st.age_days === 'number' ? st.age_days : null;
  const at = (st.last_analyzed || '').slice(0, 10);
  const survey = st.as_of ? ` Measured at the survey of ${st.as_of.slice(0, 10)}, not today.` : '';
  const title = (extra = '') => `title="${esc(`${extra}${survey}`.trim())}"`;
  switch (st.state) {
    case 'current':
      return span(OK, '✓', `analyzed ${age}d ago`, `data-stats-state="current" ${title(at ? `Last analyzed ${at}.` : '')}`)
        + (withDate && datedAge(st) ? ` <span class="tnum text-ink-muted">${esc(datedAge(st))}</span>` : '');
    case 'stale':
      return span(WARN, '◐', `stale ${age}d`, `data-stats-state="stale" ${title(at ? `Last analyzed ${at}.` : '')}`)
        + (withDate && datedAge(st) ? ` <span class="tnum text-ink-muted">${esc(datedAge(st))}</span>` : '');
    case 'never':
      return span(WARN, '○', 'never analyzed', `data-stats-state="never" ${title()}`);
    case 'reset':
      return span(MUTED, '?', `counters reset since${monthDay(st.stats_reset) ? ` ${monthDay(st.stats_reset)}` : ''}`,
        `data-stats-state="reset" ${title('The counters were reset, so whether it was analyzed before the reset is not known from this survey.')}`);
    case 'never_reset_unknown':
      return span(WARN, '○', 'not analyzed · reset time not read', `data-stats-state="never_reset_unknown" ${title()}`);
    case 'partitioned':
      return span(MUTED, '·', 'partitioned · see partitions',
        `data-stats-state="partitioned" ${title('Autovacuum does not analyze a partitioned parent; its partitions carry the statistics.')}`);
    default:
      return span(MUTED, '?', 'not collected', 'data-stats-state="not_collected"');
  }
}

/** The text for a "could not be read / not measured" response. */
function unreadable(resp, what) {
  if (resp && resp.__error) {
    return `<div class="text-caveat text-state-warn" data-stats-unreadable>${what} could not be read: ${esc(resp.__error)}</div>`;
  }
  const why = ((resp && resp.reasons) || []).join(', ').replace(/_/g, ' ');
  // The reason, only where the stored rows show it: what the credential could read (the latest probe on
  // record) and which survey stored nothing. Never a guess at a cause the rows do not carry.
  const sc = resp && resp.scope;
  const sees = sc && sc.partial ? (sc.schema_fraction || sc.fraction) : '';
  const run = resp && resp.run;
  const from = run ? ` the survey of ${monthDay(run.surveyed_at)} (${run.source === 'local' ? "RE's own" : run.source || 'unknown source'})` : ' the survey';
  const reason = (resp && (resp.reasons || []).includes('no_table_activity'))
    ? `${from} stored no table activity${sees ? ` · latest credential probe: ${sees}` : ''}` : '';
  return `<div class="text-caveat text-ink-muted" data-stats-not-measured title="${esc([sc && sc.partial && sc.probe_note, resp && (resp.notes || [])[0]].filter(Boolean).join(' · '))}"><span class="font-glyph" aria-hidden="true">?</span> ${
    what} not measured${why ? ` · ${esc(why)}` : ''}${reason ? ` ·${esc(reason)}` : ''}</div>`;
}

function resetWords(r) {
  if (!r) return 'counters reset time not read';
  if (r.state === 'reset_at') return `counters reset ${monthDay(r.at)}`;
  if (r.state === 'never_reset') return 'counters never reset';
  return 'counters reset time not read';
}

/** A tick only for a fully measured, fully current set: nothing behind, nothing unknowable, nothing uncollected. */
function allClear(d) {
  return d.total > 0 && d.behind === 0 && d.reset_tables === 0 && d.not_collected === 0;
}

/** The whole-database line (At a glance). */
export function statsLineHtml(resp) {
  if (!resp || resp.__error || !resp.currency) return unreadable(resp, 'Statistics currency');
  const c = resp.currency, d = c.database;
  const survey = monthDay(c.as_of);
  const title = `title="Ages are measured at the survey time (${esc(c.as_of)}), not today. Stale means analyzed more than ${
    c.stale_days} days before the survey."`;
  if (!d.total) {
    return `<div class="text-answer text-ink" data-stats-line ${title}>${esc(`statistics · at survey ${survey} · no tables`)} ${
      `<span class="text-ink-muted font-glyph" aria-hidden="true">∅</span>`}</div>`;
  }
  const clear = allClear(d);
  const glyph = clear ? '✓' : d.behind ? '◐' : '?';
  const bits = [
    'statistics', `at survey ${survey}`, resetWords(d.counters_reset),
    `${d.behind} of ${d.total} ${d.total === 1 ? 'table' : 'tables'} not analyzed in ${c.stale_days} days`,
  ];
  if (d.reset_tables) bits.push(`${d.reset_tables} unknowable since reset`);
  if (d.not_collected) bits.push(`${d.not_collected} not collected`);
  if (d.partitioned) bits.push(`${d.partitioned} partitioned not counted`);
  return `<div class="text-answer text-ink" data-stats-line ${title}>${esc(bits.join(' · '))} <span class="${clear ? 'text-state-ok' : d.behind ? 'text-state-warn' : 'text-ink-muted'} font-glyph" aria-hidden="true">${glyph}</span></div>`;
}

/** The rollup text for one schema row: "3 of 40 never · oldest 41d". */
export function schemaRollupWords(s) {
  if (s.empty) return s.partitioned ? `${s.partitioned} partitioned · see partitions` : 'no tables';
  const bits = [`${s.never} of ${s.total} never`];
  if (s.stale) bits.push(`${s.stale} stale`);
  if (s.reset_tables) bits.push(`${s.reset_tables} reset`);
  if (s.not_collected) bits.push(`${s.not_collected} not collected`);
  bits.push(s.oldest_days == null ? 'none analyzed' : `oldest ${s.oldest_days}d`);
  return bits.join(' · ');
}

/** Schema rollups (Contents). */
export function schemaRollupsHtml(resp) {
  if (!resp || resp.__error || !resp.currency) return unreadable(resp, 'Statistics by schema');
  const rows = resp.currency.schemas || [];
  if (!rows.length) return '';
  return `<div class="mt-s3" data-stats-schemas><div class="mb-s1 text-caveat text-ink">Statistics by schema</div>${
    rows.map((s) => {
      const bad = !s.empty && (s.never || s.stale);
      const clear = !s.empty && s.never === 0 && s.stale === 0 && s.reset_tables === 0 && s.not_collected === 0;
      return `<div class="text-caveat" data-stats-schema="${esc(s.schema)}"><span class="font-mono">${esc(s.schema)}</span> · ${
        span(clear ? OK : bad ? WARN : MUTED, s.empty ? '∅' : clear ? '✓' : bad ? '◐' : '?', schemaRollupWords(s))}</div>`;
    }).join('')}</div>`;
}

/** The never / stale counts across surveys (Over time). */
export function statsOverTimeHtml(resp) {
  if (!resp || resp.__error || !resp.currency) return unreadable(resp, 'Statistics currency over time');
  const pts = resp.history || [];
  const measured = pts.filter((p) => p.measured);
  if (measured.length < 2) {
    return `<div class="text-caveat text-ink-muted" data-stats-time-one>${measured.length
      ? 'Only one survey stored table activity, so there is no change to show yet.'
      : 'No survey stored table activity.'}</div>`;
  }
  const rows = pts.map((p) => {
    const when = esc((p.run && p.run.surveyed_at) || '');
    return p.measured
      ? `<tr data-stats-time-row><td class="pr-s3 tnum">${when}</td><td class="pr-s3 tnum">${p.never} never</td><td class="pr-s3 tnum">${
        p.stale} stale</td><td class="pr-s3 tnum">${p.reset_tables} reset</td><td class="text-ink-muted">${esc(resetWords(p.counters_reset))}</td></tr>`
      : `<tr data-stats-time-row><td class="pr-s3 tnum">${when}</td><td colspan="4" class="text-ink-muted"><span class="font-glyph" aria-hidden="true">?</span> not collected</td></tr>`;
  }).join('');
  return `<div class="mt-s3" data-stats-time><div class="mb-s1 text-caveat text-ink">Statistics currency by survey</div><table class="text-caveat">${rows}</table></div>`;
}
