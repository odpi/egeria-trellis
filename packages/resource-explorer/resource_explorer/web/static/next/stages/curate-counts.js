/* Counts for the stage-specific leading tabs (Curate: Contents, Structure).
 *
 * ONE SOURCE (the RE working rules, section 5b): a number on a tab label and the same number in its section header
 * come from the functions in this file, nothing else. The section headers call these too (curate.js
 * `savedCountHtml`, curate-scope.js `scopeHeaderText`), so the two cannot drift.
 *
 * ABSENCE IS NOT ZERO. Every function answers one of four things, never a bare number:
 *   { state: 'count',    n }   read, and this is how many (n may be 0: read, none)
 *   { state: 'reading' }       the data this count comes from has not arrived yet
 *   { state: 'unknown' }       it arrived, but does not carry this count (an older server)
 *   { state: 'none_yet' }      this kind of resource has no such parts to count
 * `countWords()` turns that into a cue key plus a short word. No function here returns 0 for "not read".
 *
 * Pure: no DOM, no state, no fetch. Arguments in, answer out. */

const count = (n) => ({ state: 'count', n });
const READING = Object.freeze({ state: 'reading' });
const UNKNOWN = Object.freeze({ state: 'unknown' });
const NONE_YET = Object.freeze({ state: 'none_yet' });
const FAILED = Object.freeze({ state: 'failed' });   // the read was tried and did not arrive: never 0, never "reading" for ever

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

/** Contents . N chosen. Repo: files + folders in the scope view's manifest (what `savedCountHtml` prints).
 *  Database: schemas whose effective choice is "catalogue" (what `scopeHeaderText` prints). `view` is the
 *  scope view as the controller holds it; null/undefined = not read yet. */
export function contentsChosen(kind, view, failed = false) {
  if (kind === 'repo') {
    const m = view && view.manifest;
    if (!m) return failed ? FAILED : READING;
    return count((m.files || 0) + (m.folders || 0));
  }
  if (kind === 'db') {
    const c = view && view.counts;
    if (!c || !isNum(c.schemas_catalogue)) return failed && !view ? FAILED : READING;
    return count(c.schemas_catalogue);
  }
  return NONE_YET;
}

/** Contents . M undecided. Repo: candidate/container rows with no choice of their own. Database: the
 *  server's own `schemas_undecided`. */
export function contentsUndecided(kind, view, failed = false) {
  if (kind === 'repo') {
    if (!view || !Array.isArray(view.rows)) return failed ? FAILED : READING;
    return count(view.rows.filter((r) => (r.candidate || r.role === 'container') && !r.choice).length);
  }
  if (kind === 'db') {
    const c = view && view.counts;
    if (!c || !isNum(c.schemas_undecided)) return failed && !view ? FAILED : READING;
    return count(c.schemas_undecided);
  }
  return NONE_YET;
}

/** Structure . N awaiting you. Repo: the plan's `awaiting` total. The plan is read lazily and can take a
 *  long time, so until it arrives this is "reading", and a plan from a server that does not send `awaiting`
 *  is "unknown" (not 0). Database Structure is placeholders today: "none yet", not "0 awaiting". */
export function structureAwaiting(kind, plan, failed = false) {
  if (kind === 'repo') {
    if (!plan) return failed ? FAILED : READING;
    const a = plan.awaiting;
    if (isNum(a)) return count(a);
    if (a && typeof a === 'object') {
      const vals = Object.values(a);
      if (vals.length && vals.every(isNum)) return count(vals.reduce((x, y) => x + y, 0));
    }
    return UNKNOWN;
  }
  return NONE_YET;
}

/** A count as { cue, word }: the cue key is one of glyphs.js's state keys (the caller draws it with its own
 *  cue helper), the word is the short word. `noun` is "chosen", "undecided", "awaiting you". */
export function countWords(c, noun, countCue = 'measured') {
  switch (c.state) {
    case 'count': return { cue: c.n === 0 ? 'measured_zero' : countCue, word: `${c.n} ${noun}` };
    case 'reading': return { cue: 'running', word: 'reading' };
    case 'unknown': return { cue: 'not_measured', word: 'not counted' };
    case 'failed': return { cue: 'error', word: 'not read' };
    default: return { cue: 'unrun', word: 'none yet' };
  }
}

/** Which counts a leading tab carries, in order. Describe and the Understanding tabs carry none. */
export const TAB_COUNTS = {
  curate_contents: [
    { fn: (ctx) => contentsChosen(ctx.kind, ctx.view, ctx.viewFailed), noun: 'chosen' },
    { fn: (ctx) => contentsUndecided(ctx.kind, ctx.view, ctx.viewFailed), noun: 'undecided', cue: 'undecided_count' },
  ],
  curate_structure: [
    { fn: (ctx) => structureAwaiting(ctx.kind, ctx.plan, ctx.planFailed), noun: 'awaiting you' },
  ],
};

/** The words a tab label appends, as [{ cue, word, state }]. `ctx` = { kind, view, plan }. Empty for a tab
 *  with no counts. */
export function tabCounts(tabId, ctx) {
  return (TAB_COUNTS[tabId] || []).map(({ fn, noun, cue }) => {
    const c = fn(ctx || {});
    return { ...countWords(c, noun, cue), state: c.state };
  });
}

/** The same words as plain text (the tooltip form, and what the harness compares with a section header). */
export function tabCountsText(tabId, ctx) {
  return tabCounts(tabId, ctx).map((c) => c.word).join(' · ');
}
