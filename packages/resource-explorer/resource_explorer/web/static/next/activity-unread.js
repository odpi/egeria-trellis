/* The Activity unread badge, and the toast that jumps to an entry (PI-124).
 *
 * Classic counted entries in browser memory since the tab was last viewed. The log is server-side and survives a
 * reload, so "unread" here means: newer than the newest entry this browser last SAW in the Activity panel
 * (localStorage, per browser; storage that is missing or throws just means nothing is remembered).
 *
 * The first visit on a browser has no mark. That is "not known", not "everything is new": the baseline is set to
 * the newest entry and nothing is flagged, instead of a badge reading 200+.
 *
 * Pure functions are exported for the tests; startActivityWatch is the only part that touches timers. */

const SEEN_KEY = 're.activity.seenTs';
export const POLL_MS = 30000;
const TOAST_ID = 'activity-toast';
const TERMINAL = new Set(['ok', 'success', 'error', 'failed']);

// The mark is also held in memory, so a browser whose storage throws still clears the badge on opening the panel.
let memSeen = null;
export function readSeen() {
  try { return globalThis.localStorage?.getItem(SEEN_KEY) || memSeen; } catch { return memSeen; }
}
export function writeSeen(ts) {
  if (!ts) return;
  memSeen = ts;
  try { globalThis.localStorage?.setItem(SEEN_KEY, ts); } catch { /* not remembered across reloads */ }
}
/** Test seam: forget the in-memory mark. */
export function resetSeenForTests() { memSeen = null; }

const time = (ts) => { const n = Date.parse(ts ?? ''); return Number.isNaN(n) ? null : n; };

/** The newest readable timestamp (as stored), or null. */
export function newestTs(entries) {
  let best = null; let bestN = null;
  for (const e of entries || []) {
    const n = time(e?.ts);
    if (n !== null && (bestN === null || n > bestN)) { best = e.ts; bestN = n; }
  }
  return best;
}

/** Entries strictly newer than the mark. No mark means nothing is flagged; an entry with no readable time is never
 *  counted as new (it cannot be shown to be). */
export function unreadEntries(entries, seenTs) {
  const seen = time(seenTs);
  if (seen === null) return [];
  return (entries || []).filter((e) => { const n = time(e?.ts); return n !== null && n > seen; });
}

/** '' for none, the number to 9, then '9+'. */
export function badgeLabel(n) {
  if (!n) return '';
  return n > 9 ? '9+' : String(n);
}

export function renderBadge(doc, n) {
  const el = doc.getElementById('activity-unread');
  if (!el) return;
  const label = badgeLabel(n);
  el.textContent = label ? `${label} new` : '';
  el.classList.toggle('hidden', !label);
  el.setAttribute('aria-label', label ? `${n} new activity entries` : '');
}

/** The panel was opened and showed `entries`: they are seen now. */
export function markSeen(doc, entries) {
  const ts = newestTs(entries);
  if (ts) writeSeen(ts);
  renderBadge(doc, 0);
}

export function closeToast(doc) {
  doc.getElementById(TOAST_ID)?.remove();
}

/** One toast at a time. `fresh` is the new terminal entries, newest first; View opens the newest. */
export function showActivityToast(doc, fresh, { onView, ttlMs = 12000 } = {}) {
  closeToast(doc);
  if (!fresh.length) return null;
  const top = fresh[0];
  const failed = ['error', 'failed'].includes(String(top.status || '').toLowerCase());
  const el = doc.createElement('div');
  el.id = TOAST_ID;
  el.setAttribute('role', 'status');
  el.className = 'fixed bottom-s4 right-s4 z-50 flex max-w-[420px] items-baseline gap-s2 rounded-sm border '
    + 'border-rule-strong bg-paper px-s3 py-s2 text-caveat text-ink shadow-lg';
  const more = fresh.length > 1 ? ` (+${fresh.length - 1} more)` : '';
  const summary = String(top.summary || top.operation || 'Activity').slice(0, 140);
  el.innerHTML = `<span class="${failed ? 'text-state-warn' : 'text-state-ok'}" aria-hidden="true">${failed ? '✗' : '✓'}</span>
    <span data-toast-text></span>
    <button type="button" data-toast-view class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[1px] text-caveat text-accent-ink">View</button>
    <button type="button" data-toast-close class="cursor-pointer bg-transparent text-ink-muted" aria-label="Dismiss">×</button>`;
  // textContent, never innerHTML: the summary is a stored string.
  el.querySelector('[data-toast-text]').textContent = `${summary}${more}`;
  doc.body.appendChild(el);
  el.querySelector('[data-toast-view]').addEventListener('click', () => { closeToast(doc); onView?.(top); });
  el.querySelector('[data-toast-close]').addEventListener('click', () => closeToast(doc));
  if (ttlMs) setTimeout(() => { if (doc.getElementById(TOAST_ID) === el) el.remove(); }, ttlMs);
  return el;
}

/** One poll step, separated from the timer so a test can drive it. Returns what it concluded. */
export function applyPoll(doc, entries, state, { onView } = {}) {
  let seen = readSeen();
  if (!seen) {
    // First visit on this browser: no mark means "not known". Baseline to the newest entry, flag nothing.
    seen = newestTs(entries);
    writeSeen(seen);
  }
  const unread = unreadEntries(entries, seen);
  renderBadge(doc, unread.length);
  const announced = state.announced || (state.announced = new Set());
  const fresh = unread.filter((e) => !announced.has(e.id) && TERMINAL.has(String(e.status || '').toLowerCase()))
    .sort((a, b) => time(b.ts) - time(a.ts));
  fresh.forEach((e) => announced.add(e.id));
  if (fresh.length && !doc.getElementById('activity-panel')) showActivityToast(doc, fresh, { onView });
  return { unread: unread.length, announced: fresh.length };
}

/** Start polling. `first` is the page the boot already fetched, so the badge is right before the first tick. */
export function startActivityWatch({ doc = document, first = [], fetchEntries, openPanel, intervalMs = POLL_MS } = {}) {
  const st = {};
  const onView = (entry) => { openPanel?.({ focusId: entry?.id }); };
  // Entries already on the page at boot are not "new": announce nothing for them.
  st.announced = new Set((first || []).map((e) => e.id));
  applyPoll(doc, first, st, { onView });
  const tick = async () => {
    try { applyPoll(doc, await fetchEntries(), st, { onView }); } catch { /* the badge keeps its last value */ }
  };
  const timer = setInterval(tick, intervalMs);
  return { stop: () => clearInterval(timer), tick, state: st };
}
