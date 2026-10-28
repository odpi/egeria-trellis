/* Formatting shared by the shell and the work-list grid.
 *
 * `ago` lived in app.js and the grid needs it too. app.js already imports
 * worklist.js, so worklist.js cannot import app.js back — hence a third
 * module rather than a second copy that would drift. */

/** "5d ago" / "3h ago" / "just now" — or "" when there is no timestamp,
 *  which is a real state and must not render as "now". */
/** Milliseconds for a registry timestamp, or NaN. The registry writes
 *  three spellings -- `...Z`, `...+00:00`, and NAIVE (`datetime.utcnow()`
 *  with no zone) -- and Date.parse reads a naive ISO string as LOCAL time,
 *  so a naive UTC stamp compared with a zoned one is off by the zone
 *  offset in whichever direction the viewer sits. Every comparison and
 *  every age goes through here; a naive stamp is UTC. */
export function whenMs(iso) {
  if (!iso) return NaN;
  const s = String(iso);
  const zoned = /(Z|[+-]\d\d:?\d\d)$/.test(s);
  return Date.parse(zoned ? s : `${s}Z`);
}

/** A stamp as the READER's local time ("Oct 16, 10:42 AM"), never the UTC date the server stored. Empty when the
 *  stamp cannot be read. */
export function localTime(iso) {
  const ms = whenMs(iso);
  if (Number.isNaN(ms)) return '';
  return new Date(ms).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

export function ago(iso) {
  if (!iso) return '';
  const then = whenMs(iso);
  if (Number.isNaN(then)) return '';
  const secs = Math.max(0, (Date.now() - then) / 1000);
  if (secs < 90) return 'just now';
  const mins = secs / 60;
  if (mins < 90) return `${Math.round(mins)}m ago`;
  const hours = mins / 60;
  if (hours < 36) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/** Days since `iso`, or null when there is no usable timestamp. Callers use
 *  this to decide staleness; `ago` is for showing it. */
export function daysSince(iso) {
  if (!iso) return null;
  const then = whenMs(iso);
  if (Number.isNaN(then)) return null;
  return (Date.now() - then) / 86400000;
}

/** One disposition verdict, one line, the same in both views:
 *  `tracking · 32d ago (2026-08-11) · who · reason`. The reason sits in
 *  ink beside the word it explains -- a trail exists for the reasons, and
 *  rendering them dimmer than the one-word verdict says the opposite.
 *  `esc` is passed in because this module has no DOM helpers of its own. */
export function verdictLineHtml(r, esc) {
  const when = r.decided_at || '';
  const rel = ago(when);
  return `<span class="text-ink">${esc(r.disposition || '—')}</span>${
    rel ? ` · <span class="tnum">${esc(rel)}</span>` : ''}${
    when ? ` <span class="tnum">(${esc(String(when).slice(0, 10))})</span>` : ''}${
    r.decided_by ? ` · ${esc(r.decided_by)}` : ''}${
    r.reason ? ` · <span class="text-ink">${esc(r.reason)}</span>` : ''}${
    depthOfferText(r.depth_offer, esc)}`;
}

/** " · depth offered, declined" / " · depth offered, 3 run" / " · depth
 *  offered, chose 2" -- the offer's outcome lives on the verdict it was
 *  made against, so a corpus of declines can be read off the trail. */
export function depthOfferText(d, esc) {
  if (!d || !d.outcome) return '';
  const n = (d.analysis_ids || []).length;
  const what = d.outcome === 'declined' ? 'declined'
    : d.outcome === 'accepted' ? `<span class="tnum">${n}</span> run`
    : d.outcome === 'chose' ? `chose <span class="tnum">${n}</span>` : esc(String(d.outcome));
  return ` · <span class="text-ink-muted">depth offered, ${what}</span>`;
}

/** "changed once" / "changed 3 times" -- never `time(s)`. */
export function changedTimesHtml(n) {
  if (n < 1) return '';
  return n === 1 ? 'changed once' : `changed <span class="tnum">${n}</span> times`;
}

/** The result line for something saved in RE's own record: "saved · who · when" (REPLY-DESIGNER-SAVE-AND-PUBLISH-VERBS.md).
 *  It is built from the re-read row (or the route's own answer), never from the click. A missing author is said, not
 *  skipped: "who isn't recorded". The Egeria family's lines are different words ("cataloged · read back when",
 *  "published · read back when", "sent · waiting for Egeria") and only after a read-back proves them. */
export function savedLine(who, whenIso) {
  const w = String(whenIso || '');
  const when = w.length >= 16 ? `${w.slice(5, 10)} ${w.slice(11, 16)}` : '';
  return ['saved', who || "who isn't recorded", when].filter(Boolean).join(' · ');
}

/** "read back 09:12": the time the read-back was done, for the Egeria family's result lines. */
export function readBackAt(date = new Date()) {
  return `read back ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

/* ── Egeria reset: words that read the `egeria_reset` marker (never decided here) ──────────────────────────
 * The server derives `published_state` ('published' | 'published_earlier' | '') and `egeria_reset_at` from the
 * marker row; these functions only choose how to say it. A short word on the element, the sentence on demand
 * (the title). */

export const PUBLISHED_EARLIER_WORD = 'published earlier';
export const PUBLISHED_EARLIER_SENTENCE = 'published earlier \u00b7 Egeria was reset';
export const STORED_COPY_RESET_LINE = 'Egeria was reset since \u00b7 the element is not in Egeria now';
export const UNBOUND_WORDS = 'unbound by reset \u00b7 rebind to recreate';

/** The Published badge for something with `last_published_at`: {text, title, earlier}. Empty text when never
 *  published. `earlier` is true only when the server says the publish predates the reset. */
export function publishedBadge(item) {
  const at = item && item.last_published_at;
  if (!at) return { text: '', title: '', earlier: false };
  if (item.published_state === 'published_earlier') {
    return { text: PUBLISHED_EARLIER_WORD, title: `${PUBLISHED_EARLIER_SENTENCE} (${at}; reset ${item.egeria_reset_at || ''})`, earlier: true };
  }
  return { text: 'Published', title: `Last published: ${at}`, earlier: false };
}

/** The heading of RE's own stored copy of a survey report, and the ONE added line when the reset postdates the
 *  read. `rep.reset_since` comes from the server. Returns {head, resetLine} (resetLine '' when none). */
export function storedCopyWords(rep) {
  const read = rep && (rep.stored_copy_read_at || rep.read_at) || '';
  return {
    head: `stored copy \u00b7 read from Egeria ${read || 'at an unrecorded time'}`,
    resetLine: rep && rep.reset_since ? STORED_COPY_RESET_LINE : '',
  };
}
