/* Curate bands that are the same on every resource kind.
 *
 * REPLY-DESIGNER-CURATE-AND-UNDERSTANDING-ALL-KINDS.md §1-2 and
 * wireframes/CurateAndUnderstanding.dc.html (page 16): Curate is "make it
 * findable and reusable", so every kind gets
 *   1. Findable  -- group (with its change control inline) and tags (chips,
 *                   autocomplete from GET /api/curate/tags). Brief T (2026-10-10):
 *                   tags are public InformalTags and the group is a Folio in
 *                   Egeria; each chip and the group line carry a state cue read
 *                   from GET /api/curate/egeria-state (the server's plan, from
 *                   the outbox rows) -- never from the click.
 *   2. the kind's own work -- repo: the existing plan view (curate.js);
 *                   database: "What gets catalogued" (the scope tree,
 *                   curate-scope.js) first, then two sections that wait on
 *                   readers, each saying what for; file system: one sentence.
 *   3. What people say -- ratings as COUNTS (never an average), and notes as
 *                   the signed append-only journal, with Classic's curator
 *                   notes read-only beneath it. Brief F (2026-10-11): ONE
 *                   rating per person is shown (their newest; earlier ones sit
 *                   behind "earlier ratings ▸"), and every rating and journal
 *                   entry carries its Egeria state as a cue plus a short word,
 *                   read from GET /api/curate/feedback-state (the server's plan).
 *
 * Brief F, one Publish per page: no band here sends anything to Egeria. Each
 * Save records in RE and shows "saved · will publish"; the page's Publish (the
 * last band on tab 1) sends tags, the group and the PRESSER's own ratings and
 * notes, as them. After a press the bands re-read ('re:published').
 *
 * Rules this file keeps (CURATE-UI-DATABASES-IMPLEMENTED.md):
 *  - what a list shows is what the server returned on a re-read AFTER a write,
 *    never what the click handler assumed; a status sentence is derived from
 *    those rows;
 *  - every write control is disabled, with a plain reason, when nobody is
 *    signed in (the routes answer 401 then);
 *  - an author label is always shown. The server supplies `author_label`;
 *    this file falls back to the same words rather than ever drawing blank;
 *  - nothing here reads back from Egeria and nothing here writes to it; the
 *    one exception is a person's "retry" of a dead tag or group write.
 */
import { ago, savedLine } from '/static/next/format.js';
import { stateEntry } from '/static/next/glyphs.js';
import {
  getCurateTagsDetail, getCurateAllTags, addCurateTag, removeCurateTag,
  getCurateEgeriaState, syncCurateEgeria, getCurateFeedbackState,
  getCurateFeedback, addCurateFeedback, getCurateNotes, deleteCurateNote,
  assignGroup, listGroups, getDataClassRules, getQualityProposals, postQualityVerdict, postQualityTableComparison, getQualityRules, postQualityPublish,
  getFilesystemReview, putFilesystemReviewVerdict, publishFilesystemDataClasses,
} from '/static/re-api.js';
import { mountQualityChecks } from '/static/next/stages/quality-checks.js';
import {
  state, esc, tnum, refreshGroupsAndSidebar, openCurate,
  renderJournalWrite, renderJournalEntries,
} from '/static/next/app.js';

export const UNSIGNED_LABEL = 'unsigned · from before authors were recorded';
const FEEDBACK_CATEGORIES = [
  ['', 'Category (optional)'], ['quality', 'Quality'], ['documentation', 'Documentation'],
  ['usefulness', 'Usefulness'], ['other', 'Other'],
];

const whoAmI = () =>
  (state.me && (state.me.user_id || state.me.username || state.me.egeria_user)) || '';
const stars = (n) => '★'.repeat(n);
const label = (row) => row.author_label || (row.author ? row.author : UNSIGNED_LABEL);
const day = (iso) => String(iso || '').replace('T', ' ').slice(0, 10);
const newestFirst = (rows) =>
  [...rows].sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
/** The reason shown beside a write control that is disabled because nobody is signed in. */
const signInReason = (what) => `sign in to ${what} — it needs an author`;
const stale = (el, slug) => !el.isConnected || slug !== state.selectedSlug;

const bandHead = (title, extra = '') => `<div class="mb-s1 flex flex-wrap items-baseline gap-x-s2 text-caps uppercase tracking-caps text-ink-muted">
  <span class="text-ink">${esc(title)}</span>${extra}</div>`;

/* ── Band 2, database: present, explained, never an empty table ──────── */

const DB_WORK_SECTIONS = [
  {
    id: 'glossary',
    title: 'Glossary terms on tables and columns',
    waits: 'No proposals yet: needs data classes per column (data_class_match) and a glossary to match against, and the tables to be published first (on Contents).',
  },
  {
    id: 'schema-match',
    title: 'Logical schema match',
    waits: 'No proposals yet: needs the logical schemas Egeria knows to be read, and the tables to be published first (on Contents). Rows will be proposed, then confirmed, overridden, or flagged when a later survey disagrees.',
  },
];

/** The first section of a database's band 2: what gets catalogued (the
 *  scope tree, curate-scope.js, fills `[data-curate-scope]`). It comes first
 *  because the two sections below act on its output. */
export function databaseScopeHtml() {
  return `<div data-curate-work="scope" class="mb-s3 min-w-0">
    <div class="text-answer text-ink">What gets published</div>
    <div data-curate-scope class="mt-s1 min-w-0 max-w-full text-caveat text-ink-muted">Reading the scope…</div></div>`;
}

/** A database's Structure tab (Brief S2): the term and logical-schema proposals (placeholders until their readers
 *  exist), the data-class rules block inside the glossary section, and the dependencies line. */
export function databaseStructureHtml() {
  return DB_WORK_SECTIONS.map((s) => `<div data-curate-work="${s.id}" class="mb-s2">
    <div class="text-answer text-ink">${esc(s.title)}</div>
    <div class="text-caveat text-ink-muted">${esc(s.waits)}</div>${s.id === 'glossary' ? rulesBlockShellHtml() : ''}</div>`).join('')
    + `<div data-curate-work="quality-checks" class="mb-s2">
    <div class="text-answer text-ink">Quality checks</div>
    <div data-curate-quality class="mt-s1 min-w-0 max-w-full text-caveat text-ink-muted">Reading the proposed checks…</div></div>`
    + `<div data-curate-work="dependencies" class="mb-s2">
    <div class="text-answer text-ink">Dependencies</div>
    <div data-curate-dependencies-none class="text-caveat text-ink-muted">none detected for databases yet</div></div>`;
}

/** Proposed quality checks, with Accept | Reject (quality-checks.js draws into the slot; it fails inside itself). */
export function renderQualityBlock(slot, slug) {
  if (!slot) return null;
  return mountQualityChecks(slot, slug, { api: { getProposals: getQualityProposals, postVerdict: postQualityVerdict,
    postTableComparison: postQualityTableComparison, getRules: getQualityRules, postPublish: postQualityPublish },
  me: whoAmI, openContents: () => openCurate({ tab: 'curate_contents' }) });
}

export function databaseWorkHtml() {
  // The rules block sits INSIDE the glossary (term) section, not beside it: the section order the
  // stage pins (scope, glossary, schema-match) is unchanged.
  return databaseScopeHtml() + databaseStructureHtml();
}

/* ── PI-041: "Rules Egeria applies", read-only ───────────────────────────
 * Lists the data-class rules the server reads (`GET /api/egeria/rules/dataclasses`). That route
 * answers per rule with its own `source`: "Egeria (Active)" when the keywords were read from
 * Egeria's valid values, "Local Fallback" when the server used RE's built-in keyword list. Only the
 * first kind is called Egeria's; the second is shown apart, labelled as not read from Egeria, so the
 * block never claims a rule Egeria did not give. When nothing came from Egeria the block says "no
 * reader yet". It has no control and writes nothing. */
export function rulesBlockShellHtml() {
  return `<div data-curate-rules class="mt-s2 min-w-0">
    <div class="text-caveat text-ink">Data-class rules Egeria applies</div>
    <div data-rules-body class="text-caveat text-ink-muted">Reading the data-class rules…</div></div>`;
}

const ruleRow = (r) => `<div data-rule="${esc(r.name)}" class="mt-[2px]">
  <span class="font-semibold text-ink">${esc(r.display_name || r.name)}</span>
  <span class="text-ink-muted"> — ${esc(r.description || '')}</span>
  <div class="text-provenance text-ink-muted">keywords: <span class="font-mono">${(r.keywords || []).map(esc).join(', ') || 'none'}</span></div></div>`;

/** The block's body from the route's answer (`rules`) or its failure (`error`). Pure. */
export function rulesBodyHtml(rules, error = '') {
  if (error) {
    return `<div data-rules-state="no_reader" class="text-caveat text-ink">no reader yet — the data-class rules could not be read: ${esc(error)}</div>`;
  }
  const list = Array.isArray(rules) ? rules : [];
  // The read's one keyword-source label ("keywords: Egeria, N sets" / "keywords: built-in list ·
  // Egeria had none" / "… · could not read Egeria"), the same on every rule.
  const kwSource = (list.find((r) => r.keyword_source) || {}).keyword_source;
  const sourceLine = kwSource
    ? `<div data-rules-keyword-source class="text-provenance text-ink-muted">${esc(kwSource)}</div>` : '';
  const fromEgeria = list.filter((r) => /^egeria/i.test(String(r.source || '')));
  const builtIn = list.filter((r) => !/^egeria/i.test(String(r.source || '')));
  const local = builtIn.length
    ? `<details data-rules-local class="mt-[2px]"><summary class="cursor-pointer text-provenance text-accent-ink underline">RE's built-in data-class keyword list, not read from Egeria (${builtIn.length})</summary>${builtIn.map(ruleRow).join('')}</details>`
    : '';
  if (!fromEgeria.length) {
    return `${sourceLine}<div data-rules-state="no_reader" class="text-caveat text-ink">no reader yet — Egeria's own data-class rules were not read${
      list.length ? `; the server answered only RE's built-in data-class keyword list (${list.length} data-class rules)` : '; the server answered no data-class rules'}.</div>${local}`;
  }
  return `${sourceLine}<div data-rules-state="read" data-rules-egeria>${fromEgeria.map(ruleRow).join('')}</div>${local}`;
}

/** Fill the block. A failure says so in the block; it never throws into the pane. */
export async function renderRulesBlock(slot) {
  if (!slot) return;
  const body = slot.querySelector('[data-rules-body]');
  try {
    body.innerHTML = rulesBodyHtml(await getDataClassRules());
  } catch (err) {
    if (slot.isConnected) body.innerHTML = rulesBodyHtml(null, err.message);
  }
}

export const FILESYSTEM_WORK_SENTENCE =
  'Nothing to review for file systems yet. Group, tags, ratings and notes above and below apply.';

export function filesystemWorkHtml() {
  return `<div data-curate-work="filesystem" class="text-caveat text-ink-muted">${esc(FILESYSTEM_WORK_SENTENCE)}</div>`;
}

/* ── Band 2, file system: the proposals to review (C3) ─────────────────── */

/** Proposals listed at once; the rest wait behind "more". */
export const REVIEW_PAGE = 20;
const REVIEW_LISTS = [
  { key: 'folders', kind: 'fs_folder_description', title: 'Folder descriptions' },
  { key: 'classes', kind: 'fs_data_class', title: 'Data-class matches' },
];
const clip = (t, n) => (String(t || '').length > n ? `${String(t).slice(0, n - 1)}…` : String(t || ''));
const verdictWords = (v) => (v ? `${v.verdict === 'confirm' ? 'confirmed' : 'rejected'} by ${v.by} ${day(v.at)}` : '');

/** The collapsed line: proposals still to review, and how many are confirmed. */
export function reviewLine(review) {
  return `${review.to_review} proposal${review.to_review === 1 ? '' : 's'} to review · ${review.confirmed} confirmed`;
}

function reviewItemHtml(list, it, signedIn) {
  const what = list.kind === 'fs_data_class'
    ? `${esc(it.file)} › ${esc(it.column)} <span class="text-ink-muted">matches</span> ${esc(it.data_class)}${it.privacy_relevant ? ' <span class="text-ink-muted">· privacy-relevant</span>' : ''}`
    : `${esc(it.folder || '/')} <span class="text-ink-muted">·</span> ${esc(clip(it.title || it.description, 80))}${
      it.title && it.description ? ` <span class="text-ink-muted">— ${esc(clip(it.description, 160))}</span>` : ''}`;
  const half = (verdict, label) => `<button type="button" data-review-verdict="${verdict}" data-review-key="${esc(it.key)}"
    aria-pressed="${it.verdict && it.verdict.verdict === verdict ? 'true' : 'false'}"${signedIn ? '' : ` disabled title="${esc(signInReason('give a verdict'))}"`}
    class="cursor-pointer rounded-sm border ${it.verdict && it.verdict.verdict === verdict ? 'border-accent text-accent-ink' : 'border-rule-strong text-ink'} bg-transparent px-2 py-[1px] text-caveat disabled:cursor-default disabled:opacity-60">${label}</button>`;
  return `<div class="flex flex-wrap items-baseline gap-x-s2 py-[2px] text-caveat" data-review-item="${esc(it.key)}">
    <span class="min-w-0 text-ink">${what}</span>${half('confirm', 'Confirm')}${half('reject', 'Reject')}
    <span class="text-provenance text-ink-muted" data-review-verdict-words>${esc(verdictWords(it.verdict))}</span>
    <span class="text-provenance text-state-warn" data-review-feedback></span></div>`;
}

function reviewGoneHtml(list, it) {
  const what = list.kind === 'fs_data_class' ? `${esc(it.file)} › ${esc(it.column)} · ${esc(it.data_class)}` : esc(it.file);
  return `<div class="py-[2px] text-caveat text-ink-muted" data-review-gone="${esc(it.key)}">${what}
    <span class="text-provenance">· ${esc(it.words)} · ${esc(verdictWords(it.verdict))}${it.verdict && it.verdict.run ? ` · proposed by the run of ${esc(day(it.verdict.run))}` : ''}</span></div>`;
}

/** One list: its heading with the count, the first `shown` proposals, "more (N)" for the rest, then the verdicts whose
 *  proposal a later run did not make again ("no longer proposed"). */
function reviewListHtml(list, section, shown, signedIn) {
  const items = section.items || [];
  const gone = section.gone || [];
  if (!items.length && !gone.length) {
    return `<div data-review-list="${list.key}" class="mt-s2"><div class="text-caps uppercase tracking-caps text-ink-muted">${esc(list.title)} · none proposed</div></div>`;
  }
  const rest = Math.max(0, items.length - shown.n);
  const goneRest = Math.max(0, gone.length - shown.g);
  return `<div data-review-list="${list.key}" class="mt-s2">
    <div class="text-caps uppercase tracking-caps text-ink-muted">${esc(list.title)} · ${items.length}${section.not_kept ? ` · ${section.not_kept} more not kept by the run` : ''}</div>
    ${items.slice(0, shown.n).map((it) => reviewItemHtml(list, it, signedIn)).join('')}
    ${rest ? `<button type="button" data-review-more="${list.key}" class="cursor-pointer bg-transparent p-0 text-caveat text-accent-ink underline">more (${rest})</button>` : ''}
    ${gone.length ? `<div class="mt-s1 text-provenance text-ink-muted">${esc(section.gone.length)} no longer proposed</div>
      ${gone.slice(0, shown.g).map((it) => reviewGoneHtml(list, it)).join('')}
      ${goneRest ? `<button type="button" data-review-more-gone="${list.key}" class="cursor-pointer bg-transparent p-0 text-caveat text-accent-ink underline">more (${goneRest})</button>` : ''}` : ''}</div>`;
}

/** The whole band for a read answer: ONE line while collapsed, the lists open on demand. With nothing proposed and no
 *  verdict held, the old sentence stands (nothing to review). */
export function filesystemReviewHtml(review, shownBy, signedIn, open = false) {
  const r = review || {};
  const held = REVIEW_LISTS.some((l) => ((r[l.key] || {}).items || []).length || ((r[l.key] || {}).gone || []).length);
  if (!held) return filesystemWorkHtml();
  const confirmed = r.confirmed || 0;
  const sendButton = signedIn && confirmed > 0
    ? `<button type="button" data-fs-send-classes class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[1px] text-caveat text-accent-ink" title="Sends the ${esc(confirmed)} confirmed data-class matches to Egeria, one DataClassAnnotation each">Send confirmed data classes</button>`
    : '';
  const sendFeedback = `<span class="text-provenance text-state-warn" data-fs-send-feedback></span>`;
  const staysLine = `<div class="mt-s1 text-provenance text-ink-muted" data-fs-review-stays>${esc(r.stays || '')}</div>`;
  return `<div data-curate-work="filesystem" data-fs-review class="text-caveat">
    <details data-fs-review-details${open ? ' open' : ''}><summary class="cursor-pointer text-ink" data-fs-review-line>${esc(reviewLine(r))}</summary>
      ${staysLine}
      <div class="mt-s1 text-caveat">${sendButton}${sendFeedback}</div>
      ${REVIEW_LISTS.map((l) => reviewListHtml(l, r[l.key] || {}, shownBy[l.key], signedIn)).join('')}
    </details></div>`;
}

/** Fill the file system's band 2 from the server's review (RE's own rows). A failed read says so; it never throws into
 *  the pane. A verdict is redrawn from the server's answer, never from the click. */
export async function renderFilesystemReview(el, slug) {
  if (!el) return;
  const signedIn = !!whoAmI();
  let review = null;
  try { review = await getFilesystemReview(slug); } catch (err) {
    if (!stale(el, slug)) el.innerHTML = `<div data-curate-work="filesystem" class="text-caveat text-state-warn">The proposals could not be read: ${esc(err.message || 'unreadable')}</div>`;
    return;
  }
  if (stale(el, slug)) return;
  const shownBy = Object.fromEntries(REVIEW_LISTS.map((l) => [l.key, { n: REVIEW_PAGE, g: REVIEW_PAGE }]));
  const draw = (open) => {
    el.innerHTML = filesystemReviewHtml(review, shownBy, signedIn, open);
    const details = el.querySelector('[data-fs-review-details]');
    el.querySelectorAll('[data-review-more]').forEach((b) => b.addEventListener('click', () => { shownBy[b.dataset.reviewMore].n += REVIEW_PAGE; draw(true); }));
    el.querySelectorAll('[data-review-more-gone]').forEach((b) => b.addEventListener('click', () => { shownBy[b.dataset.reviewMoreGone].g += REVIEW_PAGE; draw(true); }));
    el.querySelectorAll('[data-review-verdict]').forEach((b) => b.addEventListener('click', async () => {
      if (b.disabled || b.dataset.pending) return;                  // a second press is ignored
      b.dataset.pending = '1';
      const list = REVIEW_LISTS.find((l) => b.closest(`[data-review-list="${l.key}"]`));
      const row = b.closest('[data-review-item]');
      try {
        const section = await putFilesystemReviewVerdict(slug, list.kind, b.dataset.reviewKey, b.dataset.reviewVerdict);
        if (!el.isConnected) return;
        review = { ...review, [list.key]: section };
        const sum = (k) => REVIEW_LISTS.reduce((n, l) => n + ((review[l.key] || {})[k] || 0), 0);
        review.to_review = sum('to_review');
        review.confirmed = sum('confirmed');
        draw(true);
      } catch (err) {
        delete b.dataset.pending;
        const fb = row && row.querySelector('[data-review-feedback]');
        if (fb) fb.textContent = `not saved · ${err.message || 'the verdict was refused'}`;
      }
    }));
    // Wire up the send confirmed data classes button
    const sendBtn = el.querySelector('[data-fs-send-classes]');
    if (sendBtn) {
      sendBtn.addEventListener('click', async () => {
        if (sendBtn.disabled || sendBtn.dataset.pending) return;     // a second press is ignored
        sendBtn.disabled = true;
        sendBtn.dataset.pending = '1';
        const fbEl = el.querySelector('[data-fs-send-feedback]');
        try {
          const result = await publishFilesystemDataClasses(slug);
          if (!el.isConnected) return;
          if (result.nothing_new) {
            fbEl.textContent = 'nothing new to send';
          } else if (result.sent > 0) {
            fbEl.textContent = `sent ${result.sent} confirmed data classes`;
          } else {
            fbEl.textContent = `not sent · ${result.words || 'nothing was sent'}`;
          }
        } catch (err) {
          if (fbEl) fbEl.textContent = `not sent · ${err.message || 'the send was refused'}`;
        } finally {
          sendBtn.disabled = false;
          delete sendBtn.dataset.pending;
        }
      });
    }
    return details;
  };
  draw(false);
}

/* ── Band 1: Findable ─────────────────────────────────────────────────── */

function currentGroupSlug(slug) {
  const rows = state.resourceType === 'db' ? state.databases
    : state.resourceType === 'filesystem' ? state.filesystems : state.projects;
  return ((rows || []).find((r) => r.slug === slug) || {}).group_slug || '';
}

const groupName = (gslug) =>
  gslug ? ((state.groups || []).find((g) => g.slug === gslug)?.display_name || gslug) : 'Ungrouped';

/* Brief T: each tag and the group carry their state in Egeria as a glyph plus a short word (the owner's rule:
 * a cue on the element, the sentence on demand in `title`). The state key comes from the server's plan. */
/* Brief F: "saved · will publish" is a stored cue (□), "published 10-11" a check, "not sent · <reason>" the
 * partial cue (◐). The words are the server's. */
export const EGERIA_CUE = {
  in_egeria: 'catalogued', not_yet: 'stored', not_sent: 'stored', pending: 'queued', retrying: 'queued',
  failed: 'partial', unlinked: 'gone', unread: 'unknown',
};
/** The words beside a cue: the server's word (cut short; the reason in full in `title`). */
export function egeriaWord(item) {
  if (!item) return 'could not read';
  const w = String(item.word || item.state || '').replace(/\s+/g, ' ').trim();
  return w.length > 90 ? `${w.slice(0, 89)}…` : w;
}
/** One cue. The tone class is a literal in each branch (no class interpolation): ok, warn, or muted. */
export function egeriaCue(item, attr) {
  const key = item ? (EGERIA_CUE[item.state] || 'unknown') : 'unknown';
  const e = stateEntry(key);
  const open = e.tone === 'text-state-ok' ? '<span class="text-state-ok"'
    : e.tone === 'text-state-warn' ? '<span class="text-state-warn"' : '<span class="text-ink-muted"';
  const title = item && item.reason && item.state !== 'pending' ? item.reason : egeriaWord(item);
  return `${open} ${attr} data-cue="${esc(key)}" data-egeria-state="${esc(item ? item.state : 'unread')}" title="${esc(title)}"><span class="font-glyph" aria-hidden="true">${e.glyph}</span> ${esc(egeriaWord(item))}</span>`;
}
const retryButton = (item) => (item && item.retry
  ? ` <button type="button" data-curate-egeria-retry="${esc(item.kind)}" data-name="${esc(item.name)}" class="cursor-pointer bg-transparent p-0 text-accent-ink underline">retry</button>`
  : '');

export async function renderFindableBand(el, slug, entityType, status = '') {
  if (!el) throw new Error('Findable band host missing');
  if (status === '') el.innerHTML = `<div class="text-caveat text-ink-muted">Reading tags…</div>`;
  let tags;
  let allTags = [];
  let egeria = null;
  let egeriaErr = '';
  try {
    [tags, allTags, egeria] = await Promise.all([
      getCurateTagsDetail(entityType, slug),
      getCurateAllTags().catch(() => []),
      getCurateEgeriaState(entityType, slug).catch((err) => { egeriaErr = err.message || 'unreadable'; return null; }),
    ]);
    if (!(state.groups || []).length) {
      try { state.groups = (await listGroups()) || []; } catch { /* the select says there are none */ }
    }
  } catch (err) {
    if (stale(el, slug)) return;
    el.innerHTML = `${bandHead('Findable')}<div class="text-caveat text-state-warn">Tags could not be read: ${esc(err.message)}</div>`;
    return;
  }
  if (stale(el, slug)) return;

  const me = whoAmI();
  const gslug = currentGroupSlug(slug);
  const groups = state.groups || [];
  const options = [`<option value="">— Ungrouped —</option>`]
    .concat(groups.map((g) => `<option value="${esc(g.slug)}" ${g.slug === gslug ? 'selected' : ''}>${esc(g.display_name || g.slug)}</option>`));
  if (gslug && !groups.some((g) => g.slug === gslug)) {
    options.push(`<option value="${esc(gslug)}" selected>${esc(gslug)}</option>`);
  }
  const items = (egeria && egeria.items) || [];
  const itemOf = (kind, name) => items.find((i) => i.kind === kind && i.name === name) || null;
  // When the state could not be read, every cue says so rather than drawing nothing (absence is not "not in Egeria").
  const cueFor = (kind, name, attr) => (egeria ? egeriaCue(itemOf(kind, name), attr) : egeriaCue(null, attr));
  const chips = tags.length
    ? tags.map((t) => `<span data-curate-tag="${esc(t.tag)}" title="added by ${esc(label(t))}"
        class="inline-flex items-baseline gap-[4px] rounded-sm border border-rule-strong px-[6px] py-[1px] text-caveat text-ink">${esc(t.tag)}
        <button type="button" data-curate-tag-remove="${esc(t.tag)}" aria-label="remove tag ${esc(t.tag)}"
          ${me ? '' : `disabled title="${esc(signInReason('remove a tag'))}"`}
          class="${me ? 'cursor-pointer' : 'opacity-60'} bg-transparent p-0 text-ink-muted">×</button></span>
        <span class="text-provenance">${cueFor('tag', t.tag, `data-curate-tag-state="${esc(t.tag)}"`)}${retryButton(itemOf('tag', t.tag))}</span>`).join(' ')
    : `<span class="text-caveat text-ink-muted">No tags yet.</span>`;
  // Tags removed in RE that Egeria still has, or had: the unlink's own state (pending, failed with retry, unlinked).
  const removed = items.filter((i) => i.kind === 'tag' && !i.desired);
  const removedLine = removed.length
    ? `<div data-curate-tags-removed class="mb-s1 flex flex-wrap items-baseline gap-s2 text-provenance">
        <span class="text-ink-muted">removed</span>
        ${removed.slice(0, 8).map((i) => `<span data-curate-removed-tag="${esc(i.name)}"><span class="line-through text-ink-muted">${esc(i.name)}</span> ${egeriaCue(i, `data-curate-tag-state="${esc(i.name)}"`)}${retryButton(i)}</span>`).join(' ')}
      </div>`
    : '';
  const leftGroups = items.filter((i) => i.kind === 'group' && !i.desired);
  const groupCue = gslug ? `<span class="text-provenance">${cueFor('group', gslug, 'data-curate-group-state')}${retryButton(itemOf('group', gslug))}</span>` : '';
  const leftLine = leftGroups.length
    ? `<div data-curate-groups-left class="mb-s1 flex flex-wrap items-baseline gap-s2 text-provenance">
        <span class="text-ink-muted">left</span>
        ${leftGroups.slice(0, 4).map((i) => `<span data-curate-left-group="${esc(i.name)}"><span class="text-ink-muted">${esc(i.label || i.name)}</span> ${egeriaCue(i, `data-curate-group-left-state="${esc(i.name)}"`)}${retryButton(i)}</span>`).join(' ')}
      </div>`
    : '';
  // Brief F: no send control here. What is not in Egeria yet goes with the page's Publish (the last band).
  const egeriaLine = egeriaErr
    ? `<div data-curate-egeria-unread class="mb-s1 text-provenance text-state-warn">Egeria state could not be read: ${esc(egeriaErr)}</div>`
    : '';

  el.innerHTML = `${bandHead('Findable', `<span class="normal-case tracking-normal">· tags are public in Egeria · the group is a Folio · sent with Publish</span>`)}
    <div class="mb-s1 flex flex-wrap items-baseline gap-s2 text-caveat">
      <span class="text-ink-muted">RE group</span>
      <span data-curate-group-now class="text-ink">${esc(groupName(gslug))}</span>${groupCue}
      <select data-curate-group-select aria-label="group" class="rounded-sm border border-rule-strong bg-transparent px-1 text-caveat text-ink">${options.join('')}</select>
      <button type="button" data-curate-group-save class="cursor-pointer bg-transparent p-0 text-accent-ink underline">change</button>
      <span class="text-provenance text-ink-muted">${groups.length ? 'Admin keeps the full group manager' : 'no groups defined yet — Admin has the group manager'}</span>
    </div>
    ${leftLine}
    <div class="mb-s1 flex flex-wrap items-baseline gap-s2">
      <span class="text-caveat text-ink-muted">tags</span>${chips}
    </div>
    ${removedLine}${egeriaLine}
    <div class="flex flex-wrap items-baseline gap-s2 text-caveat">
      <input data-curate-tag-input list="curate-tag-datalist" type="text" placeholder="＋ tag" aria-label="add a tag"
        ${me ? '' : 'disabled'}
        class="w-[16ch] rounded-sm border border-rule-strong bg-transparent px-[4px] text-caveat text-ink placeholder:text-ink-muted">
      <datalist id="curate-tag-datalist">${(allTags || []).map((t) => `<option value="${esc(t.tag)}"></option>`).join('')}</datalist>
      <button type="button" data-curate-tag-add ${me ? '' : 'disabled'}
        class="${me ? 'cursor-pointer text-accent-ink underline' : 'opacity-60 text-ink-muted'} bg-transparent p-0">Save</button>
      <span data-curate-findable-status class="text-provenance text-ink-muted">${esc(me ? status : signInReason('add or remove a tag'))}</span>
    </div>`;

  const statusEl = el.querySelector('[data-curate-findable-status]');
  const say = (msg, warn = false) => {
    if (!statusEl) return;
    statusEl.textContent = msg;
    statusEl.className = `text-provenance ${warn ? 'text-state-warn' : 'text-ink-muted'}`;
  };
  const failure = (err, what) => (err.status === 401 ? signInReason(what) : `${what} failed: ${err.message}`);

  // The words after a write come from the re-read rows, not from the click.
  const reload = async (verify) => {
    let rows;
    let st = null;
    try {
      [rows, st] = await Promise.all([getCurateTagsDetail(entityType, slug),
        getCurateEgeriaState(entityType, slug).catch(() => null)]);
    } catch (err) { say(`written, but the list could not be re-read: ${err.message}`, true); return; }
    await renderFindableBand(el, slug, entityType, verify(rows, st));
  };
  const egeriaSaid = (st, kind, name) => {
    const it = st && (st.items || []).find((i) => i.kind === kind && i.name === name);
    return st ? (it ? ` · Egeria: ${egeriaWord(it)}` : '') : ' · Egeria state could not be re-read';
  };

  const addTag = async () => {
    const input = el.querySelector('[data-curate-tag-input]');
    const tag = (input.value || '').trim();
    if (!tag) { input.focus(); return; }
    try { await addCurateTag(entityType, slug, tag); }
    catch (err) { say(failure(err, 'add the tag'), true); return; }
    await reload((rows, st) => {
      const mine = rows.find((r) => r.tag === tag.toLowerCase());
      return mine ? `${savedLine(mine.author_label || mine.author, mine.created_at)} · tag “${tag.toLowerCase()}” is on the list${egeriaSaid(st, 'tag', tag.toLowerCase())}`
        : `the write returned, but “${tag.toLowerCase()}” is not in the re-read list`;
    });
  };
  el.querySelector('[data-curate-tag-add]').addEventListener('click', addTag);
  el.querySelector('[data-curate-tag-input]').addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') { ev.preventDefault(); addTag(); }
  });
  el.querySelectorAll('[data-curate-tag-remove]').forEach((b) => b.addEventListener('click', async () => {
    const tag = b.dataset.curateTagRemove;
    try { await removeCurateTag(entityType, slug, tag); }
    catch (err) { say(failure(err, 'remove the tag'), true); return; }
    await reload((rows, st) => (rows.some((r) => r.tag === tag)
      ? `“${tag}” is still on the list after the removal`
      : `tag “${tag}” is off the list${egeriaSaid(st, 'tag', tag)}`));
  }));

  // A person's retry of ONE dead link/unlink (a new write; the server refuses anything else). Destructive
  // unlinks are never re-sent on their own, so this explicit, visible press is their only retry.
  const egeriaAct = async (button, retry) => {
    button.disabled = true;
    try { await syncCurateEgeria(entityType, slug, retry); }
    catch (err) { button.disabled = false; say(failure(err, 'retry'), true); return; }
    await reload((rows, st) => `retried ${retry.kind} “${retry.name}”${egeriaSaid(st, retry.kind, retry.name)}`);
  };
  el.querySelectorAll('[data-curate-egeria-retry]').forEach((b) => b.addEventListener('click', () =>
    egeriaAct(b, { kind: b.dataset.curateEgeriaRetry, name: b.dataset.name })));

  el.querySelector('[data-curate-group-save]').addEventListener('click', async () => {
    const want = el.querySelector('[data-curate-group-select]').value;
    let saved;
    try { saved = await assignGroup(slug, want, entityType); }
    catch (err) { say(failure(err, 'change the group'), true); return; }
    try { await refreshGroupsAndSidebar(); } catch { /* the re-read below says what the row now holds */ }
    if (stale(el, slug)) return;
    const now = currentGroupSlug(slug);
    await renderFindableBand(el, slug, entityType,
      now === want ? `${savedLine((saved || {}).saved_by, (saved || {}).saved_at)} · group is now ${groupName(now)}`
        : `the change returned, but the list still shows ${groupName(now)}`);
  });
}

/* ── Band 3: What people say ─────────────────────────────────────────── */

/** Brief F: one cue per feedback state (the server's `feedback_egeria` states). */
export const FEEDBACK_CUE = {
  saved: 'stored', waiting: 'optional', sending: 'queued', published: 'catalogued', not_sent: 'partial',
  not_permitted: 'no_access', replaced: 'archived', kept: 'optional', unread: 'unknown',
};
/** A rating's or a journal entry's state in Egeria: a cue and the server's short word (the reason in `title`). */
export function feedbackCue(item, attr = '') {
  const key = item ? (FEEDBACK_CUE[item.state] || 'unknown') : 'unknown';
  const e = stateEntry(key);
  const open = e.tone === 'text-state-ok' ? '<span class="text-state-ok"'
    : e.tone === 'text-state-warn' ? '<span class="text-state-warn"' : '<span class="text-ink-muted"';
  const word = item ? String(item.word || item.state) : 'Egeria state could not be read';
  const short = word.length > 90 ? `${word.slice(0, 89)}…` : word;
  return `${open} ${attr} data-cue="${esc(key)}" data-feedback-state="${esc(item ? item.state : 'unread')}" title="${esc(word)}"><span class="font-glyph" aria-hidden="true">${e.glyph}</span> ${esc(short)}</span>`;
}

/** Addendum C (owner, 2026-10-11): ONE rating per person counts and shows: their newest rated entry (from the
 *  rows; the server's plan gives each its cue, and an older rating stays "published" until the newer one lands). */
export function currentRatings(feedback, _states = null) {
  const seen = new Set();
  return newestFirst(feedback).filter((f) => {
    if (!f.author || f.rating == null || f.rating === '') return true;
    if (seen.has(f.author)) return false;
    seen.add(f.author);
    return true;
  });
}

export function ratingsHeadline(feedback, states = null) {
  feedback = currentRatings(feedback, states);
  const rated = feedback.filter((f) => f.rating != null && f.rating !== '');
  const unrated = feedback.length - rated.length;
  const levels = [5, 4, 3, 2, 1]
    .map((n) => [n, rated.filter((f) => Number(f.rating) === n).length])
    .filter(([, c]) => c > 0);
  const counts = rated.length
    ? `${rated.length} · ${levels.map(([n, c]) => `${stars(n)} ${c}`).join(' · ')}`
    : 'none yet';
  return `${counts}${unrated ? ` · ${unrated} comment${unrated === 1 ? '' : 's'} without a rating` : ''}`;
}

function feedbackEntryHtml(f, item, stateRead = true) {
  return `<div data-curate-feedback="${esc(f.id || '')}" class="border-t border-rule py-s1">
    <div class="text-caveat text-ink">${f.rating ? `<span class="text-ink">${esc(stars(Number(f.rating)))}</span> ` : ''}${
      f.category ? `<span class="text-ink-muted">${esc(f.category)}</span> — ` : ''}${tnum(esc(f.message))}</div>
    <div class="text-provenance text-ink-muted">${esc(label(f))} · <span class="tnum">${esc(day(f.created_at))}</span>${
      stateRead || item ? ` · ${feedbackCue(item, 'data-curate-feedback-cue')}` : ''}</div>
  </div>`;
}

export async function renderRatingsBand(el, slug, entityType, status = '') {
  if (!el) throw new Error('Ratings band host missing');
  if (status === '') el.innerHTML = `<div class="text-caveat text-ink-muted">Reading ratings…</div>`;
  let feedback;
  let fstate = null;
  try {
    [feedback, fstate] = await Promise.all([getCurateFeedback(entityType, slug),
      getCurateFeedbackState(entityType, slug).catch(() => null)]);
  } catch (err) {
    if (stale(el, slug)) return;
    el.innerHTML = `${bandHead('Ratings')}<div class="text-caveat text-state-warn">Ratings could not be read: ${esc(err.message)}</div>`;
    return;
  }
  if (stale(el, slug)) return;
  const me = whoAmI();
  const dis = me ? '' : `disabled title="${esc(signInReason('rate or comment'))}"`;
  const states = fstate ? Object.fromEntries((fstate.ratings || []).map((i) => [i.id, i])) : null;
  const current = newestFirst(currentRatings(feedback, states));
  const earlier = newestFirst(feedback.filter((f) => !current.includes(f)));
  const entry = (f) => feedbackEntryHtml(f, states ? states[f.id] : null, !!states);
  el.innerHTML = `${bandHead('Ratings', `<span data-curate-ratings-headline class="normal-case tracking-normal text-ink">· ${esc(ratingsHeadline(feedback, states))} · one per person · public in Egeria</span>`)}
    ${current.map(entry).join('')}
    ${earlier.length ? `<details data-curate-earlier-ratings class="mt-s1"><summary class="cursor-pointer text-provenance text-ink-muted">earlier ratings ▸ <span class="tnum">${earlier.length}</span></summary>${earlier.map(entry).join('')}</details>` : ''}
    <div class="mt-s2 flex flex-wrap items-baseline gap-s2 text-caveat">
      <span class="text-ink-muted">rate it</span>
      <select data-curate-fb-rating aria-label="rating" ${dis} class="rounded-sm border border-rule-strong bg-transparent px-1 text-caveat text-ink">
        <option value="">No rating</option>
        ${[5, 4, 3, 2, 1].map((n) => `<option value="${n}">${stars(n)}</option>`).join('')}
      </select>
      <select data-curate-fb-category aria-label="category" ${dis} class="rounded-sm border border-rule-strong bg-transparent px-1 text-caveat text-ink">
        ${FEEDBACK_CATEGORIES.map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join('')}
      </select>
    </div>
    <textarea data-curate-fb-message rows="2" ${dis} placeholder="How well did it serve you? A message is required."
      class="mt-s1 w-full rounded-sm border border-rule-strong bg-transparent p-s2 text-caveat text-ink placeholder:text-ink-muted"></textarea>
    <div class="mt-s1 flex items-baseline gap-s3">
      <button type="button" data-curate-fb-submit ${dis}
        class="${me ? 'cursor-pointer' : 'opacity-60'} rounded-sm border border-accent bg-transparent px-2 py-[2px] text-caveat text-accent-ink">Save rating</button>
      <span class="text-provenance text-ink-muted">a newer rating replaces yours · sent with Publish</span>
      <span data-curate-ratings-status class="text-provenance text-ink-muted">${esc(me ? status : signInReason('rate or comment'))}</span>
    </div>`;
  const say = (msg, warn) => {
    const s = el.querySelector('[data-curate-ratings-status]');
    s.textContent = msg; s.className = `text-provenance ${warn ? 'text-state-warn' : 'text-ink-muted'}`;
  };
  el.querySelector('[data-curate-fb-submit]').addEventListener('click', async () => {
    const message = el.querySelector('[data-curate-fb-message]').value.trim();
    if (!message) { say('write a message — feedback needs one', true); el.querySelector('[data-curate-fb-message]').focus(); return; }
    const r = el.querySelector('[data-curate-fb-rating]').value;
    const category = el.querySelector('[data-curate-fb-category]').value;
    const b = el.querySelector('[data-curate-fb-submit]'); b.disabled = true;
    try { await addCurateFeedback(entityType, slug, { rating: r ? Number(r) : null, category, message }); }
    catch (err) {
      b.disabled = false;
      say(err.status === 401 ? signInReason('rate or comment') : `not saved: ${err.message}`, true);
      return;
    }
    let rows;
    try { rows = await getCurateFeedback(entityType, slug); }
    catch (err) { b.disabled = false; say(`saved, but the list could not be re-read: ${err.message}`, true); return; }
    await renderRatingsBand(el, slug, entityType,
      (() => {
        const mine = [...rows].reverse().find((x) => x.message === message);
        return mine ? `${savedLine(mine.author_label || mine.author, mine.created_at)} · saved · will publish`
          : 'the save returned, but the entry is not in the re-read list';
      })());
  });
}

/** Classic's curator notes: read-only, newest first, beneath the journal.
 *  Only an UNSIGNED note can be deleted from here (a signed one has an author
 *  to protect, and the server answers 409). */
export async function renderClassicNotes(el, slug, entityType, status = '') {
  if (!el) throw new Error('Classic notes host missing');
  let notes;
  try { notes = await getCurateNotes(entityType, slug); }
  catch (err) {
    if (stale(el, slug)) return;
    el.innerHTML = `<div class="text-caveat text-state-warn">Curator notes from Classic could not be read: ${esc(err.message)}</div>`;
    return;
  }
  if (stale(el, slug)) return;
  if (!notes.length) { el.innerHTML = status ? `<div class="text-provenance text-ink-muted">${esc(status)}</div>` : ''; return; }
  const me = whoAmI();
  const unsigned = notes.filter((n) => !n.authored).length;
  el.innerHTML = `<div class="mb-s1 text-caps uppercase tracking-caps text-ink-muted">Curator notes from Classic · <span class="tnum">${notes.length}</span> · ${
    unsigned === notes.length ? 'unsigned' : `<span class="tnum">${unsigned}</span> unsigned`}</div>
    ${newestFirst(notes).map((n) => `<div data-curate-classic-note="${esc(n.id)}" class="border-t border-rule py-s1">
      <div class="text-caveat text-ink">${tnum(esc(n.note))}</div>
      <div class="text-provenance text-ink-muted">${esc(label(n))} · <span class="tnum">${esc(day(n.created_at))}</span>${
        n.authored ? '' : ` · <button type="button" data-curate-note-delete="${esc(n.id)}" ${me ? '' : `disabled title="${esc(signInReason('delete a note'))}"`}
          class="${me ? 'cursor-pointer text-accent-ink underline' : 'opacity-60 text-ink-muted'} bg-transparent p-0">delete</button>${
          me ? '' : ` <span>${esc(signInReason('delete a note'))}</span>`}`}
        <span data-curate-note-confirm="${esc(n.id)}"></span></div>
    </div>`).join('')}
    <div data-curate-notes-status class="text-provenance text-ink-muted">${esc(status)}</div>`;
  el.querySelectorAll('[data-curate-note-delete]').forEach((b) => b.addEventListener('click', () => {
    const id = b.dataset.curateNoteDelete;
    const slot = el.querySelector(`[data-curate-note-confirm="${id}"]`);
    slot.innerHTML = ` Delete this unsigned note? <button type="button" data-yes class="cursor-pointer bg-transparent p-0 text-accent-ink underline">delete</button> <button type="button" data-no class="cursor-pointer bg-transparent p-0 text-ink underline">keep</button>`;
    slot.querySelector('[data-no]').addEventListener('click', () => { slot.innerHTML = ''; });
    slot.querySelector('[data-yes]').addEventListener('click', async () => {
      try { await deleteCurateNote(id); }
      catch (err) {
        slot.innerHTML = ` <span class="text-state-warn">${esc(err.status === 401 ? signInReason('delete a note') : err.message)}</span>`;
        return;
      }
      let rows;
      try { rows = await getCurateNotes(entityType, slug); } catch { rows = null; }
      await renderClassicNotes(el, slug, entityType,
        rows && !rows.some((n) => n.id === id) ? 'note deleted' : 'the delete returned, but the note is still in the list');
    });
  }));
}

/** Notes: the journal (the same component and data as Disposition's) plus
 *  Classic's notes beneath it. */
/** Brief F: each journal entry's state in Egeria (a Note in the asset's NoteLog), drawn into the entry's own
 *  `[data-journal-cue]` slot from the server's plan. A failed read says so on every entry. */
export async function markJournalCues(root, slug, entityType) {
  const slots = [...root.querySelectorAll('[data-journal-cue]')];
  if (!slots.length) return;
  let st = null;
  try { st = await getCurateFeedbackState(entityType, slug); } catch { st = null; }
  if (!root.isConnected) return;
  const byId = st ? Object.fromEntries((st.notes || []).map((i) => [i.id, i])) : {};
  slots.forEach((s) => { s.innerHTML = ` · ${feedbackCue(st ? byId[s.dataset.journalCue] || null : null, 'data-curate-note-cue')}`; });
}

export async function renderNotesBand(el, slug, entityType) {
  if (!el) throw new Error('Notes band host missing');
  el.innerHTML = `${bandHead('Notes', `<span class="normal-case tracking-normal">· the journal · why it matters, and to whom · append-only · each entry a public Note in Egeria, sent with Publish</span>`)}
    <div id="journal-write"></div>
    <div id="journal-entries" class="mt-s2 text-caveat text-ink-muted">Reading the journal…</div>
    <div data-curate-classic-notes class="mt-s3"></div>`;
  renderJournalWrite(slug, entityType);
  // A save re-draws the entries (app.js); the cues follow every draw, read again from the server's plan.
  if (state.curateOnJournal) el.ownerDocument.removeEventListener('re:journal-drawn', state.curateOnJournal);
  state.curateOnJournal = (ev) => {
    const d = ev.detail || {};
    if (!el.isConnected) { el.ownerDocument.removeEventListener('re:journal-drawn', state.curateOnJournal); return; }
    if (d.slug === slug && d.entityType === entityType) markJournalCues(el, slug, entityType);
  };
  el.ownerDocument.addEventListener('re:journal-drawn', state.curateOnJournal);
  await Promise.all([
    renderJournalEntries(slug, entityType),
    renderClassicNotes(el.querySelector('[data-curate-classic-notes]'), slug, entityType),
  ]);
}

/** Band 3 as a whole: ratings, then notes. */
export async function renderPeopleBand(el, slug, entityType) {
  if (!el) throw new Error('What-people-say band host missing');
  el.innerHTML = `${bandHead('What people say')}
    <div data-curate-ratings class="mb-s3"></div>
    <div data-curate-notes></div>`;
  await Promise.all([
    renderRatingsBand(el.querySelector('[data-curate-ratings]'), slug, entityType),
    renderNotesBand(el.querySelector('[data-curate-notes]'), slug, entityType),
  ]);
}

/** The frame every kind shares (G1 added `publish`, the Egeria band, after the kind's own work). Returns the band hosts; the kind's own
 *  work goes in `kind`. `findable` and `people` are filled by the callers
 *  below so that a repo can fill `people` after its (slow) plan loads. */
export function bandFrameHtml(withMeasures = false) {
  const measures = withMeasures ? '<section data-curate-band="measures" class="mb-s4 border-b border-rule pb-s2"></section>' : '';
  return `<section data-curate-band="findable" class="mb-s4 border-b border-rule pb-s2"></section>
    <section data-curate-band="kind" class="mb-s4"></section>
    ${measures}
    <section data-curate-band="publish" class="mb-s4 border-b border-rule pb-s2"></section>
    <section data-curate-band="people" class="mb-s4"></section>`;
}
