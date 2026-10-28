/* Curate . By analysis: the Analytic Library's `curation_coverage`, rendered.
 *
 * The routine (trellis_analytics.routines.curation_coverage) does the counting, against the same node set the
 * Contents tab uses; this file only draws what it returned. It computes no count of its own.
 *
 * ABSENCE IS NOT ZERO: `not_established` and `not_applicable` show their state and reason and NO table; a
 * table of zeros would say "we looked and found nothing". `nothing_found` is the one honest empty, and it
 * says so in the routine's own words.
 *
 * Pure: a result in, HTML out. No DOM, no fetch. */
import { STATES } from '/static/next/glyphs.js';

const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/** The envelope state as a glyph from the one table plus a short word. Literal tone classes, so the CSS build sees them. */
const CUES = {
  measured: { entry: STATES.measured, word: 'measured' },
  nothing_found: { entry: STATES.nothing, word: 'nothing found' },
  not_established: { entry: STATES.not_established, word: 'not established' },
  not_applicable: { entry: STATES.unclassified, word: 'not applicable' },
};

export function stateCueHtml(state, reason = '') {
  const c = CUES[state] || { entry: STATES.not_established, word: 'could not read' };
  const attrs = `data-cue="${esc(state)}" title="${esc(reason || c.word)}"`;
  const inner = `<span class="font-glyph" aria-hidden="true">${c.entry.glyph}</span> ${esc(c.word)}</span>`;
  // Two literal class strings, never an interpolated one, so the CSS build and its coverage test can read them.
  return c.entry.tone === 'text-state-ok'
    ? `<span class="text-state-ok" ${attrs}>${inner}`
    : `<span class="text-ink-muted" ${attrs}>${inner}`;
}

function lineHtml(l) {
  const names = (l.names || []).length
    ? `<details class="inline ml-s2"><summary class="inline cursor-pointer text-ink-muted">which</summary>`
      + `<span class="block text-caveat text-ink-muted">${l.names.map(esc).join(', ')}</span></details>` : '';
  return `<li data-coverage-line="${esc(l.key)}" data-count="${esc(l.count)}">`
    + `<span>${esc(l.count)}</span> ${esc(l.label)}${names}</li>`;
}

function groupHtml(g) {
  const lines = (g.lines || []).filter((l) => l.count > 0 || g.key !== 'blocked');
  return `<section class="mt-s4" data-coverage-group="${esc(g.key)}">`
    + `<h3 class="text-answer">${esc(g.words)}</h3>`
    + (lines.length ? `<ul class="mt-s1 list-none pl-0">${lines.map(lineHtml).join('')}</ul>` : '')
    + `</section>`;
}

/** @param result the route's dict (a CoverageResult), or null when the read failed. */
export function coverageHtml(result) {
  if (!result || !result.envelope) {
    return `<div data-coverage data-coverage-state="not_established" class="mt-s3">${stateCueHtml('not_established')}`
      + ` <span class="text-ink-muted">the coverage could not be read</span></div>`;
  }
  const state = result.envelope.result_state;
  const reason = result.envelope.result_state_reason || '';
  const head = `<div class="flex flex-wrap items-baseline gap-s3">${stateCueHtml(state, reason)}`
    + `<span class="text-caps uppercase tracking-caps text-ink-muted">Curation coverage</span>`
    + (result.as_of ? `<span class="text-caveat text-ink-muted" data-coverage-as-of>read back ${esc(result.as_of)}</span>` : '')
    + `</div>`;
  const body = (state === 'measured' || state === 'nothing_found')
    ? (result.groups || []).map(groupHtml).join('')
    : (reason ? `<p class="mt-s2 text-ink-muted" data-coverage-reason>${esc(reason)}</p>` : '');
  const reasonLine = state === 'nothing_found' && reason
    ? `<p class="mt-s2 text-ink-muted" data-coverage-reason>${esc(reason)}</p>` : '';
  return `<div data-coverage data-coverage-state="${esc(state)}" class="mt-s3">${head}${reasonLine}${body}</div>`;
}
