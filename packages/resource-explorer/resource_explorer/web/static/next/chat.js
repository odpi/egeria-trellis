/* Chat — the "Ask" rail and the answer pane it opens.
 *
 * Moved out of app.js (PLAN-FINISH-REPOS.md item 9 — "Chat, under-utilised").
 * Chrome-level, like worklist.js and rfa.js: chat is not one of the eight
 * stages, it sits beside whichever one is active, scoped to the selected
 * resource. Unlike rfa.js it DOES import state/esc/$/etc. back from app.js —
 * the same choice stages/understanding.js already made (see that file's own
 * header comment) — because a turn's rendering is inseparable from the
 * pane/rail chrome app.js owns, and duplicating that chrome here would be
 * the two-copies bug this project keeps finding in its own docs.
 *
 * ASSESSMENT-CHAT.md's finding: almost nothing here was unbuilt. Session
 * continuity, `sourceLine()`, the compile id, the vote hash, Plotly charts
 * and server-rendered Mermaid diagrams all worked already — squeezed into a
 * 290px rail. SPEC-THE-STAGE-PAGE.md ruled that shape wrong for analyses
 * ("one place for the fact, the pane under the answer; the rail keeps
 * provenance only") and chat was "the last surface still doing the thing
 * that ruling fixed." This file applies the same rule here:
 *
 *   - the rail is the turn list, the ask box, and provenance — question,
 *     source line, vote, copy-as-evidence, "open the compile";
 *   - the PANE is where an answer is actually read: prose, chart, diagram or
 *     table alike, at full width, via `promoteToPane()` (still in app.js —
 *     shared with the Questions-checklist row-promotion path, so a chat
 *     chart and a fact's chart cannot drift into two renderers).
 *
 * Three follow-ups from ASSESSMENT-CHAT.md §3, in priority order:
 *   (a) open the compile — `turn.compiled` (the full {text, manifest,
 *       derivation}, not just the id) is now kept on the turn and openable
 *       into the rail's evidence slot via `openCompile()`.
 *   (b) use the stream — `askStream()` (re-api.js) drives `submitAsk()`,
 *       falling back to the blocking `ask()` only if the stream itself
 *       fails before any text arrived.
 *   (c) join the vote to the gaps loop — `vote()` now ALSO calls
 *       `submitAnswerFeedback()` (the same `/api/feedback/answer` endpoint
 *       item 8's Questions-checklist bar already posts to), so a chat "not
 *       helpful" lands as a `disagree` verdict and raises a gap the same way
 *       a disputed checklist answer does. Only when the turn has a resource
 *       in scope — see `vote()`'s own comment for why an unscoped turn
 *       cannot join this collection, which is a real boundary, not an
 *       oversight.
 */
import {
  ask, askStream, sendFeedback, submitAnswerFeedback, compileContext, runAnalysis, pollActivity,
  activityFailure, failureText, getSchedules, saveSchedule, addAlias, listAliases, writeJournalTurn,
} from '/static/re-api.js';
import { stateEntry } from '/static/next/glyphs.js';
import { showMarkdown } from '/static/next/markdown.js';
import {
  state, esc, icon, tnum, $, selectResourceFromChat,
  ensureRailShowing, railFrame, railClaim, openMembers,
  promoteToPane, answerForm, copyAsEvidence, apiEntityType,
  feedbackVotesHtml,
} from '/static/next/app.js';

/* A browser-generated id, so the agent can keep cross-turn memory.
 *
 * Computed on FIRST USE, not at module scope: this module is imported by
 * app.js before `localStorage` access is safe in every embedding (a private
 * window can throw on first touch), so the read is deferred to the first
 * question actually asked. */
let _sessionId = null;
function sessionId() {
  if (_sessionId) return _sessionId;
  try {
    _sessionId = localStorage.getItem('re-next.sessionId') || '';
  } catch { _sessionId = ''; }
  if (!_sessionId) {
    _sessionId = (crypto.randomUUID && crypto.randomUUID()) || `s-${Date.now()}-${Math.random()}`;
    try { localStorage.setItem('re-next.sessionId', _sessionId); } catch { /* not fatal */ }
  }
  return _sessionId;
}

/**
 * The rail is a chat DRAWER with a transcript, not a single question box.
 *
 * A first pass showed one question and one answer, because that is what the
 * static mock showed. History is not a nicety: the whole argument for the
 * sidecar is that a session accumulates — you ask, you narrow, you ask again
 * — and each answer is evidence you may want to cite later.
 *
 * Turns are labelled with the resource they were asked about, because the
 * transcript outlives the selection and an answer about a different repo
 * that is not marked as such is worse than no answer.
 */
function railScopeText() {
  return state.selectedSlug ? `scoped to ${esc(state.selectedSlug)}` : 'no resource selected';
}

/** The rail's scope line follows the selection. renderRail() runs at boot
 *  and on clear only -- re-running it on every selection would wipe the
 *  chat and the evidence slot -- so the line is updated on its own. It
 *  read "scoped to amundsen" under a pane showing egeria_python (owner's
 *  screenshots, 2026-09-13). */
export function renderRailScope() {
  const el = $('rail-scope');
  if (el) el.innerHTML = railScopeText();
}

export function renderRail() {
  $('rail').innerHTML = `
    <div class="mb-s3 flex items-baseline gap-s2">
      <span class="font-heading uppercase tracking-caps text-caps text-accent-on-dark">Ask</span>
      <span id="rail-scope" class="text-caps text-chrome-muted">${railScopeText()}</span>
      ${state.chat.length ? `<span class="ml-auto flex items-center gap-s2">
        <button data-act="copy-transcript" title="Copy the whole transcript as markdown, with each answer's source line"
          class="cursor-pointer bg-transparent text-caps text-chrome-muted hover:text-chrome-ink"
          >${icon('copy', { size: 13 })} transcript</button>
        <button data-act="clear-chat"
          class="cursor-pointer bg-transparent text-caps text-chrome-muted underline hover:text-chrome-ink"
          >clear</button>
      </span>` : ''}
    </div>

    <div id="rail-evidence" class="mb-s3"></div>
    <div id="chat-log" class="mb-s3 flex flex-col gap-s3"></div>

    <textarea id="ask-input" rows="3" placeholder="Ask about this resource…"
      class="mb-s2 w-full rounded-sm border border-chrome-line bg-transparent p-s2 text-subtab
             text-chrome-ink placeholder:text-chrome-muted"></textarea>
    <div class="flex items-baseline gap-s2">
      <button id="ask-submit"
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-[10px] py-[4px]
               text-chip text-accent-on-dark">Ask</button>
      <button id="ask-compile" type="button"
        title="Show which stored results would answer this question, and which analyses have not run, without asking it"
        class="cursor-pointer bg-transparent p-0 text-caps text-accent-on-dark underline">what would answer this?</button>
      <span class="text-caps text-chrome-muted">⌘/Ctrl + Enter</span>
    </div>`;

  $('ask-submit').addEventListener('click', () => submitAsk());
  $('ask-compile').addEventListener('click', previewCompile);
  $('ask-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submitAsk();
  });
  $('rail').querySelector('[data-act="clear-chat"]')?.addEventListener('click', () => {
    state.chat = [];
    if (state.promoted && state.chat.indexOf(state.promoted) === -1) state.promoted = null;
    renderRail();
  });
  $('rail').querySelector('[data-act="copy-transcript"]')?.addEventListener('click', (e) =>
    copyAsEvidence(state.chat.map(turnAsMarkdown).join('\n\n---\n\n'), e.currentTarget));
  renderTurnList();
}

/** One turn's source footer.
 *
 *  A REQUIREMENT, not decoration: an answer composed from survey metadata
 *  and an answer retrieved from embeddings must not look alike. When the
 *  response does not say which, this says THAT — it never guesses, and it
 *  never quietly implies retrieval. */
export function sourceLine(body) {
  const manifest = body.compiled && body.compiled.manifest;
  if (manifest && typeof manifest === 'object') {
    // What the answer was compiled FROM is the manifest's `packed` list (one
    // entry per stored result used). The manifest's own FIELD NAMES
    // (compile_id, budget, headroom ...) are bookkeeping, not sources; they
    // were listed here once and read as if they were the answer.
    const packed = Array.isArray(manifest.packed) ? manifest.packed : null;
    const keys = packed ? [...new Set(packed.map(packedLabel).filter(Boolean))] : [];
    // The absence fact `evidenceFooterListsHtml` deliberately does not
    // enumerate belongs here instead, as one clause on the answer's own
    // sentence -- not a footer line per key, and not one combined line
    // naming every key either (REVIEW-SURVEY-PANE-285.md's correction:
    // "the footer lists only lists that exist ... a combined line naming
    // twelve analysis ids is the same wall, shorter"). Said once, generically,
    // only when packed evidence had list-shaped sections and NONE had a
    // member reader to open.
    const lists = listSentences(body);
    const noListsAvailable = lists.length > 0 && !lists.some((l) => l.members);
    const suffix = noListsAvailable ? ' · no evidence lists were available for this question' : '';
    const narr = narrativesClause(manifest);
    if (!packed) return `Compiled evidence is on file · its sources are not listed${suffix}${narr}`;
    return (keys.length
      ? `From ${keys.length} stored result${keys.length === 1 ? '' : 's'} · ${keys.join(', ')}`
      : 'Compiled evidence was empty · answered from retrieval') + suffix + narr;
  }
  // Answered from stored rows with no compile behind them: said as what it was read from, never "not reported".
  const from = body.answered_from;
  if (from && from.kind === 'stored_dependencies' && from.resource_slug) {
    return `From the stored dependency list of ${from.resource_slug} · ${from.analysis_id || 'dependency_analysis'}`;
  }
  return 'No compiled evidence on this answer · source not reported';
}

/** A packed entry's name: the analysis, and its resource when a multi-resource compile packed it (the same analysis
 *  can be packed once per resource). */
function packedLabel(p) {
  return p && p.key ? (p.resource_slug ? `${p.key} (${p.resource_slug})` : p.key) : '';
}

/** The file-system narrative sections a compile reports (`manifest.narratives`; a multi-resource compile keeps one per
 *  resource under `per_resource`). Each: {slug, state, packed, passages}. */
export function narrativeSections(manifest) {
  if (!manifest || typeof manifest !== 'object') return [];
  const one = (n, slug) => (n && typeof n === 'object' && n.state
    ? [{ slug: slug || '', state: n.state, packed: !!n.packed, passages: Array.isArray(n.passages) ? n.passages : [] }] : []);
  if (manifest.narratives) return one(manifest.narratives, '');
  const per = manifest.per_resource && typeof manifest.per_resource === 'object' ? manifest.per_resource : {};
  return Object.entries(per).flatMap(([slug, m]) => one(m && m.narratives, slug));
}

/** One narrative section in words: what the answer could cite, or why it could not. Never "no narratives". */
export function narrativeWords(n) {
  const k = n.passages.length;
  const of = n.slug ? ` of ${n.slug}` : '';
  if (n.state === 'found') {
    return n.packed
      ? `${k} narrative passage${k === 1 ? '' : 's'}${of} it could cite, each a proposal`
      : `${k} narrative passage${k === 1 ? '' : 's'}${of} matched but ${k === 1 ? 'was' : 'were'} left out to fit the budget, so the answer cannot cite ${k === 1 ? 'it' : 'them'}`;
  }
  if (n.state === 'not_read') return `narrative files${of} not read yet`;
  if (n.state === 'no_match') return `narrative files${of} read, no passage matched this question`;
  return `narrative files${of} could not be searched (index unreachable)`;
}

/** The source line's clause for the narratives: one phrase per file system, '' when the compile had none. */
export function narrativesClause(manifest) {
  return narrativeSections(manifest).map((n) => ` · ${narrativeWords(n)}`).join('');
}

/** Mermaid source fenced inside an answer, if there is any.
 *
 *  Several analyses carry their diagram source as text (architecture_recovery
 *  writes its Mermaid into the answer), so this is how a topology answer
 *  reaches the pane without a new endpoint. */
function extractMermaid(text) {
  const m = /```mermaid\s*\n([\s\S]*?)```/.exec(String(text || ''));
  return m ? m[1].trim() : null;
}

/** The analyses an answer was compiled from that have a member list. The
 *  old "Open as candidates (N)" counted answer lines under 80 characters --
 *  it read 11 for a lead sentence and ten bullets about 32 dependencies and
 *  could deliver nothing, since dependency names match no registered
 *  resource (REPLY-BLANK-RAIL, 2026-09-12). Deleted. What an answer can
 *  honestly offer is the list it was answered FROM: the member tree of a
 *  packed evidence section, which the pane already renders in full. */
const MEMBER_LISTED = new Set(['dependency_analysis', 'cve_scan', 'data_file_profiling', 'api_structure',
  'code_symbol_extraction', 'architecture_recovery', 'sub_resource_survey', 'manifest_parse', 'interface_surface']);
function listSources(body) {
  const packed = body?.compiled?.manifest?.packed;
  if (!Array.isArray(packed)) return [];
  // The list opens for the turn's resource, so an entry packed for ANOTHER resource (multi-resource compile) is not
  // offered here: its button would open the wrong resource's list.
  return packed.filter((p) => p && p.role === 'evidence' && MEMBER_LISTED.has(p.key) && !p.resource_slug).map((p) => p.key);
}

/** The lists an answer was compiled from, with their TOTAL and what the
 *  model was shown -- one sentence each, the whole count, and a way out.
 *  The compiler records `manifest.lists[section][field] = {total, shown:
 *  {FULL, SUMMARY}}` for every packed reader-derived section, and the
 *  section's packed rung says which `shown` applies. The prose channel
 *  laundered "... and 22 more" into "here are some of them" with 32 in the
 *  sentence and 10 on the page and nothing saying which was the list
 *  (REPLY-BLANK-RAIL §2); this is the total said out loud beside the
 *  answer, from the manifest rather than recounted, so the pane's member
 *  tree and the rail agree by construction. */
function listSentences(body) {
  const m = body?.compiled?.manifest;
  const lists = m?.lists;
  if (!lists || typeof lists !== 'object' || !Array.isArray(m.packed)) return [];
  const out = [];
  for (const p of m.packed) {
    if (!p || p.role !== 'evidence' || !lists[p.key]) continue;
    const rung = String(p.rung || 'FULL').toUpperCase();
    // One sentence per SECTION. Nested lists arrive one per sub-key
    // (by_ecosystem.java, .javascript, .python); a person asked about the
    // dependencies, not about Java's, so they are summed and the sub-keys
    // counted -- "68 dependencies · in 3 ecosystems".
    const byParent = new Map();
    for (const [field, ext] of Object.entries(lists[p.key])) {
      if (!ext || typeof ext.total !== 'number') continue;
      const shown = ext.shown && typeof ext.shown === 'object'
        ? (ext.shown[rung] ?? ext.shown.FULL ?? ext.total) : ext.total;
      const dot = field.indexOf('.');
      const parent = dot > 0 ? field.slice(0, dot) : field;
      const cur = byParent.get(parent) || { total: 0, shown: 0, parts: 0 };
      cur.total += ext.total; cur.shown += shown; cur.parts += dot > 0 ? 1 : 0;
      byParent.set(parent, cur);
    }
    for (const [field, agg] of byParent) {
      out.push({ key: p.key, field, total: agg.total, shown: agg.shown, parts: agg.parts, rung,
                 members: MEMBER_LISTED.has(p.key) });
    }
  }
  return out;
}

/** "32 dependencies · by ecosystem · 10 shown to the model · the full list
 *  is in the pane". A field like `by_ecosystem.python` reads as "by
 *  ecosystem · python". */
const LIST_NOUNS = {
  dependency_analysis: ['dependency', 'dependencies'], cve_scan: ['advisory', 'advisories'],
  data_file_profiling: ['data file', 'data files'], api_structure: ['symbol', 'symbols'],
  code_symbol_extraction: ['symbol', 'symbols'], architecture_recovery: ['component', 'components'],
  sub_resource_survey: ['file or folder', 'files and folders'], manifest_parse: ['manifest entry', 'manifest entries'],
  interface_surface: ['endpoint', 'endpoints'],
};
const LIST_FIELD_NOUNS = {
  'api_structure.classes': ['class', 'classes'], 'api_structure.functions': ['public function', 'public functions'],
  'cve_scan.findings': ['finding', 'findings'], 'cve_scan.ecosystems_seen': ['ecosystem', 'ecosystems'],
};
/** Text colours for the footer by the ground it sits on. Literal class
 *  strings, so Tailwind sees them. The footer renders on the paper answer
 *  pane (the only call site); `chrome` is for a dark-ground rail. */
const FOOTER_TONES = {
  paper: { ink: 'text-ink', muted: 'text-ink-muted', link: 'text-accent-ink' },
  chrome: { ink: 'text-chrome-ink', muted: 'text-chrome-muted', link: 'text-accent-on-dark' },
};
/** [singular, plural] for one list field, or null when no noun is known. */
function listNoun(l) {
  return LIST_FIELD_NOUNS[`${l.key}.${l.field}`] || LIST_NOUNS[l.key] || null;
}
/** "49 endpoints" / "1 finding" -- the count with its noun, singular at 1. */
function countNounHtml(l) {
  const n = listNoun(l);
  const word = n ? n[l.total === 1 ? 0 : 1] : l.field.replace(/_/g, ' ');
  return `<span class="tnum">${l.total}</span> ${esc(word)}`;
}
/** One row per SECTION. A section with several list fields (cve_scan:
 *  findings + ecosystems_seen) is one row naming each field's count, one
 *  link. Then the extent: how much of it the model was shown. */
function listSentenceHtml(group, i, surface = 'paper') {
  const t = FOOTER_TONES[surface] || FOOTER_TONES.paper;
  const ls = Array.isArray(group) ? group : [group];
  const key = ls[0].key;
  const live = ls.filter((l) => l.total);
  const label = `<span data-list-label class="font-mono ${t.muted}">${esc(key.replace(/_/g, ' '))}</span> · `;
  if (!live.length) {
    return `<div data-list-empty class="mt-s2 text-chip ${t.muted}">${label}nothing stored by ${esc(key)} for this repo</div>`;
  }
  const head = live.map((l) => {
    const grp = l.parts ? ` (in <span class="tnum">${l.parts}</span> ${esc(l.field.replace(/^by_/, '').replace(/_/g, ' '))}${l.parts === 1 ? '' : 's'})` : '';
    return countNounHtml(l) + grp;
  }).join(' · ');
  const total = live.reduce((a, l) => a + l.total, 0);
  const shown = live.reduce((a, l) => a + l.shown, 0);
  const extent = shown < total
    ? `<span class="${t.muted}"><span class="tnum">${shown}</span> of <span class="tnum">${total}</span> shown to the model</span>`
    : `<span class="${t.muted}">all shown to the model</span>`;
  // Three things the designer's read of #60 fixed: the packer's rung is
  // internal and "at full" read as "shown fully"; a section with no member
  // reader rendered nothing where the link would be (the silent-omission
  // rule); and › was a text glyph doing an icon's job.
  //
  // A missing member reader is rendered ONLY here, per-section, when this is
  // called at all -- see `evidenceFooterListsHtml` below, which stops calling
  // this for readerless sections once there is more than one, so the reader
  // is not shown a wall of near-identical "no member reader" lines. This
  // function assumes `l.members` is true; a readerless `l` reaching it is a
  // caller bug, not a state to render.
  return `<div class="mt-s2 text-chip ${t.ink}">
    ${label}${head} · ${extent}${
      // The control says what pressing it does; it is the only clickable
      // part of the line, and the middot before it does the sentence
      // break's work.
      ` · <button data-list-source="${esc(key)}" data-list-slug="${esc(i)}"
          class="cursor-pointer bg-transparent p-0 ${t.link} underline">open the full list${icon('chevron-right', { size: 13 })}</button>`}
  </div>`;
}

/** `turn.lists`, filtered to sections that actually HAVE a member reader.
 *  The footer names only lists that exist to open — a readerless section is
 *  not named here at all, singly or combined; that fact belongs in the
 *  answer's own one-line provenance sentence instead (`sourceLine()`).
 *
 *  Live-reproduced 2026-09-25 (REVIEW-SURVEY-PANE-285.md, then corrected on
 *  review of the first fix): a database's compiled evidence packs many
 *  sections with list-shaped fields (coverage_signals, grain_determination,
 *  preliminary_fit, subject_signals, schema_conventions, ...) and NONE of
 *  them are in `MEMBER_LISTED` -- that set is repo-shaped analyses only.
 *  `listSentenceHtml`'s per-section fallback rendered "No list to open — X
 *  has no member reader yet." once per section, a wall of near-identical
 *  lines for one answer. The first fix collapsed that wall into one combined
 *  line naming every key — still a wall, just shorter, and still something
 *  the footer had no business asserting: a footer that lists what exists is
 *  not the place to enumerate what doesn't. */
function evidenceFooterListsHtml(lists, slug, surface = 'paper') {
  const t = FOOTER_TONES[surface] || FOOTER_TONES.paper;
  const groups = new Map();
  for (const l of lists.filter((x) => x.members)) {
    if (!groups.has(l.key)) groups.set(l.key, []);
    groups.get(l.key).push(l);
  }
  // Status from stored data, not from the model's compliance: when a list was
  // cut, say how much of it the answer could have named.
  const cut = [...groups.values()].flat().filter((l) => l.total && l.shown < l.total);
  const note = cut.length
    ? `<div data-partial-note class="mt-s2 text-chip ${t.ink}">Listed ${cut.map((l) =>
        `<span class="tnum">${l.shown}</span> of <span class="tnum">${l.total}</span> ${esc((listNoun(l) || [0, l.field.replace(/_/g, ' ')])[1])}`).join('; ')} -- open the full list for the rest</div>`
    : '';
  return note + [...groups.values()].map((g) => listSentenceHtml(g, slug, surface)).join('');
}

/**
 * The rail's turn list — QUESTION and PROVENANCE only, per SPEC-THE-STAGE-PAGE.md
 * ("the rail keeps provenance, and only provenance"). The answer body itself
 * (prose, chart, diagram, table) lives in the pane via `promoteChatTurn()` —
 * this list is how you get back to an earlier one.
 *
 * Replaces the old `renderChatLog()`, which rendered the full answer,
 * its chart/diagram "open in pane" escape hatch, and its feedback bar all
 * inside the 290px rail — exactly the shape ASSESSMENT-CHAT.md and
 * SPEC-THE-STAGE-PAGE.md both name as the thing to stop doing.
 */
/** Longest answer shown whole in the rail; past this the preview is clamped and offers "more ›". */
const RAIL_PREVIEW_CHARS = 240;
const RAIL_PREVIEW_LINES = 4;

/** The answer text on a finished rail turn: escaped (never raw HTML), wrapped so it cannot overrun at the
 *  largest text size, and clamped to a few lines when long, with "more ›" (the click opens the full turn). */
function answerPreviewHtml(answer) {
  const text = String(answer);
  const long = text.length > RAIL_PREVIEW_CHARS || text.split('\n').length > RAIL_PREVIEW_LINES;
  const style = long
    ? ` style="display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:${RAIL_PREVIEW_LINES};overflow:hidden"`
    : '';
  return `<div data-turn-answer${long ? ' data-turn-clamped' : ''} class="mb-s1 min-w-0 whitespace-pre-wrap break-words text-chip text-chrome-ink"${style}>${esc(text)}</div>${
    long ? '<div data-turn-more class="text-caps text-chrome-muted">more ›</div>' : ''}`;
}

function renderTurnList() {
  const log = $('chat-log');
  if (!log) return;
  // Turns for the resource in scope show as they come. A turn asked about
  // ANOTHER resource stays in the transcript (it outlives the selection, and
  // is still copyable) but is folded into one closed group below, so the rail
  // never opens on a different resource's conversation.
  const turnHtml = (t, i) => {
    const offScope = Boolean(t.slug) && t.slug !== state.selectedSlug;
    const isOpen = state.promoted === t;
    // A done turn with neither text nor error says so; it never falls back to listing fields.
    const noAnswer = !t.pending && !t.answer && !t.error;
    return `
    <button data-open-turn="${i}"
      class="block w-full cursor-pointer border-l-2 ${isOpen ? 'border-accent bg-chrome-line/20' : offScope ? 'border-chrome-line' : 'border-accent'}
             bg-transparent pl-s2 py-[2px] text-left">
      <div class="text-caps uppercase tracking-caps text-chrome-muted">
        You${t.slug ? ` · ${esc(t.slug)}` : ''}${offScope ? ' · not the current resource' : ''}
      </div>
      <div class="mb-s1 text-subtab text-chrome-ink">${esc(t.question)}</div>
      ${t.pending ? `<div data-turn-pending class="text-chip text-chrome-muted">${cue('running', t.streaming ? 'Answering…' : 'Asking…', '', 'chrome')}</div>` : ''}
      ${t.error ? `<div data-turn-failed class="text-chip text-accent-on-dark">failed · ${esc(t.error)}</div>` : ''}
      ${noAnswer ? '<div data-turn-no-answer class="text-chip text-chrome-muted">no answer recorded</div>' : ''}
      ${t.answer && !t.pending ? answerPreviewHtml(t.answer) : ''}
      ${t.answer && !t.pending ? `<div class="text-caps text-chrome-muted">${esc(t.source || '')}${isOpen ? ' · shown in pane' : ' · tap to open'}</div>` : ''}
    </button>`;
  };
  const here = [];
  const elsewhere = [];
  state.chat.forEach((t, i) => {
    (t.slug && t.slug !== state.selectedSlug ? elsewhere : here).push(turnHtml(t, i));
  });
  const holdsPromoted = state.chat.some((t) => state.promoted === t && t.slug && t.slug !== state.selectedSlug);
  log.innerHTML = here.join('') + (elsewhere.length
    ? `<details data-other-resources class="text-caps text-chrome-muted"${holdsPromoted ? ' open' : ''}>
        <summary class="cursor-pointer">${elsewhere.length} turn${elsewhere.length === 1 ? '' : 's'} on other resources</summary>
        <div class="mt-s2 flex flex-col gap-s3">${elsewhere.join('')}</div>
      </details>` : '');

  // The rail's answer text is markdown; it is drawn escaped first, then replaced by the sanitised rendering.
  log.querySelectorAll('[data-turn-answer]').forEach((el) => {
    const t = state.chat[Number(el.closest('[data-open-turn]')?.dataset.openTurn)];
    if (t?.answer) showMarkdown(el, t.answer);
  });
  log.querySelectorAll('[data-open-turn]').forEach((b) => b.addEventListener('click', () => {
    const t = state.chat[Number(b.dataset.openTurn)];
    if (t && (t.answer || t.error) && !t.pending) promoteChatTurn(t);
  }));
  log.scrollTop = log.scrollHeight;
}

/** vote value -> the verdict item 8's `/api/feedback/answer` endpoint takes
 *  (feedback.py's `VALID_VERDICTS`). Only `disagree` raises a gap; `agree`
 *  and `partly` still land in the feedback store, same as a checklist row's
 *  "Was this right?" bar records all three. */
const VOTE_VERDICT = { 1: 'agree', 0: 'partly', '-1': 'disagree' };

function feedbackHtml(turn, i) {
  if (turn.voted !== undefined) {
    const said = { 1: 'Marked helpful.', 0: 'Marked partly right.', '-1': 'Marked not helpful.' };
    return `<div class="mt-s2 text-caps text-chrome-muted">${esc(said[String(turn.voted)])}${
      turn.gapReason ? ` · ${esc(turn.gapReason)}` : ''}</div>`;
  }
  if (turn.voteError) {
    return `<div class="mt-s2 text-caps text-state-warn-on-dark">Vote not recorded: ${esc(turn.voteError)}</div>`;
  }
  // Thumbs, not the words `yes / partly / no`. Substituting words for a
  // conventional pictogram turned a one-glance control into reading; the
  // objection to emoji was platform variance and non-recolourability, which
  // a Lucide glyph inheriting currentColor does not have. Markup itself is
  // the shared `feedbackVotesHtml()` (app.js) — chat's dark rail passes
  // `theme: 'chrome'` and stamps `data-turn` so the click handler below
  // (`footer.querySelectorAll('[data-vote]')`) can still find which turn.
  return `<div class="mt-s2 flex flex-wrap items-center gap-s3 text-caps">
    <span class="text-chrome-muted">Was this right?</span>
    ${feedbackVotesHtml({ theme: 'chrome', dataAttr: 'turn', dataValue: i })}
  </div>`;
}

async function vote(i, value) {
  const turn = state.chat[i];
  if (!turn || !turn.queryHash) return;
  try {
    await sendFeedback(turn.queryHash, value, turn.compileId || null);
    turn.voted = value;
  } catch (err) {
    // Say it failed. A vote that silently did not record is worse than no
    // vote control, because the person believes they have reported it.
    turn.voteError = err.message;
    if (state.promoted === turn) renderPromotedFooter(turn, i);
    renderTurnList();
    return;
  }

  // ASSESSMENT-CHAT.md §3(c): join the vote to the gaps loop item 8 built,
  // the same way the Questions-checklist bar does. A chat question is
  // free text, not one of the catalog's canonical questions, so the
  // server will not find it in `_analyses_for_question` and records the
  // disagreement attributed to no analysis (feedback.py's own documented
  // behaviour for that case) — still a real gap, still counted, just not
  // chargeable to one analysis. That is an honest outcome, not a failure
  // of this wiring.
  //
  // Requires a resource in scope: `/api/feedback/answer` 404s on an
  // unknown/empty slug, and a turn asked with nothing selected has no
  // project to attach a gap to. Recorded here as a stated skip rather than
  // a silent one, same as the vote-failure branch above.
  if (turn.slug) {
    try {
      const res = await submitAnswerFeedback({
        slug: turn.slug, question: turn.question, verdict: VOTE_VERDICT[String(value)],
        sessionId: sessionId(), page: location.pathname + location.search,
        entityType: turn.entityType,
      });
      turn.gapReason = res.gap
        ? `in this project's gaps, marked ${res.gap.destination}`
        : res.gap_reason;
    } catch (err) {
      turn.gapReason = `not joined to the gaps loop: ${err.message}`;
    }
  } else {
    turn.gapReason = 'not joined to the gaps loop — no resource was in scope for this question';
  }

  if (state.promoted === turn) renderPromotedFooter(turn, i);
  renderTurnList();
}

/** One chat turn, as markdown with its source line. */
export function turnAsMarkdown(t) {
  const out = [`**${t.question}**`, ''];
  out.push(t.answer || `_${t.error || 'No answer.'}_`, '');
  const bits = [t.slug, t.source].filter(Boolean);
  if (t.intent) bits.push(`intent ${t.intent}`);
  out.push(`— ${bits.join(' · ')}`);
  return out.join('\n');
}

/** Which stored chat turn to add: the session and the exchange id the query reply carried. No text travels: the
 *  entry is DERIVED on the server from the stored exchange (journal.derive_turn_entry), the same source the chat's
 *  `write_journal` tool reads. The person's only addition is the optional note. */
function journalPayload(t, note = '') {
  return { sessionId: sessionId(), exchangeId: t.exchangeId, note };
}

/** "Add to investigation journal": the derived entry shown read-only, with [Add] [Not now] and one optional note
 *  kept apart from it. Add writes the same row the chat's `write_journal` tool writes. Local until the investigation
 *  is promoted (it has no Egeria Project to hold a Note before that). */
async function openJournalForm(turn, host) {
  const form = host.querySelector('[data-journal-form]');
  if (!form || !state.investigation) return;
  const inv = state.investigation;
  form.innerHTML = `<span class="text-caveat text-ink-muted">${cue('running', 'reading', 'Deriving the entry from this turn.')}</span>`;
  let preview;
  try {
    preview = await writeJournalTurn(inv, journalPayload(turn), { confirm: false });
  } catch (err) {
    form.innerHTML = `<span class="text-caveat text-state-warn">${cue('error', 'not shown', err.message)} ${esc(err.message)}</span>`;
    return;
  }
  if (preview.already) {
    turn.journalSaved = true;
    form.innerHTML = `<span class="text-caveat" data-journal-saved>${cue('measured', 'already in the journal', "This turn is already an entry in this investigation's journal; nothing was added.")}</span>`;
    const trig = host.querySelector('[data-act="journal-turn"]');
    if (trig) trig.outerHTML = journalTriggerHtml(turn);
    return;
  }
  form.innerHTML = `
    <div data-journal-entry class="min-w-0 whitespace-pre-wrap break-words rounded-sm border border-rule bg-paper p-s2 text-caveat text-ink">${esc(preview.body)}</div>
    <label class="mt-s2 block text-caveat text-ink-muted">Your note (optional, kept apart from the entry)
      <input data-journal-note-input type="text" maxlength="500" class="mt-[2px] w-full rounded-sm border border-rule-strong bg-paper p-s1 text-ink">
    </label>
    <div class="mt-s2 flex items-baseline gap-s3 text-caveat">
      <button data-journal-add type="button" class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-accent-ink">Add</button>
      <button data-journal-cancel type="button" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-ink">Not now</button>
      <span data-journal-note class="text-ink-muted"></span>
    </div>`;
  const note = form.querySelector('[data-journal-note]');
  // The entry is read-only and stays exactly as derived (kept in data-raw); only its display is rendered.
  showMarkdown(form.querySelector('[data-journal-entry]'), preview.body);
  form.querySelector('[data-journal-cancel]').addEventListener('click', () => { form.innerHTML = ''; });
  form.querySelector('[data-journal-add]').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const noteText = form.querySelector('[data-journal-note-input]').value.trim();
      const res = await writeJournalTurn(inv, journalPayload(turn, noteText), { confirm: true });
      turn.journalSaved = true;
      form.innerHTML = res.already
        ? `<span class="text-caveat" data-journal-saved>${cue('measured', 'already in the journal', "This turn is already an entry in this investigation's journal; nothing was added.")}</span>`
        : `<span class="text-caveat" data-journal-saved>${cue('measured', 'added to the journal', "Written to this investigation's journal. It stays in RE until the investigation is promoted to Egeria.")}</span>`;
      const trigger = host.querySelector('[data-act="journal-turn"]');
      if (trigger) trigger.outerHTML = journalTriggerHtml(turn);
    } catch (err) {
      btn.disabled = false;
      note.textContent = err.loginRequired ? 'Sign in to write in the journal.' : `Not added: ${err.message}`;
    }
  });
}

/** The per-turn control: a button until the turn is in the journal, then the state on the element. */
function journalTriggerHtml(turn) {
  // A journal exchange (the chat's own "Add this turn?") is never itself a turn to add.
  if (turn.journalProposal) return '';
  if (turn.journalSaved) return `<span data-act="journal-turn" data-journal-state="saved">${cue('measured', 'in the journal', "This turn is an entry in the current investigation's journal.")}</span>`;
  const can = Boolean(state.investigation) && turn.exchangeId != null;
  return `<button data-act="journal-turn" ${can ? '' : 'disabled'}
        title="${!state.investigation ? 'no investigation is open · choose one on the rail' : turn.exchangeId == null ? 'This turn was not stored, so it cannot be added.' : 'Show this turn as a journal entry for the current investigation'}"
        class="cursor-pointer bg-transparent text-caveat text-ink-muted underline disabled:cursor-default disabled:opacity-60"
        >add to investigation journal</button>`;
}

/** The chat's own "Add this turn to the investigation journal?" with [Add] [Not now], drawn from the journal tool's
 *  proposal. Add writes through the same route as the per-turn control. */
function journalProposalHtml(turn) {
  const st = turn.jpState || { state: 'asking' };
  const head = '<span data-jp-sentence>Add this turn to the investigation journal?</span>';
  let tail;
  if (st.state === 'saving') tail = cue('running', 'adding', 'Writing the entry.');
  else if (st.state === 'saved') tail = cue('measured', 'added to the journal', "Written to this investigation's journal.");
  else if (st.state === 'dismissed') tail = cue('optional', 'not added', 'Nothing was written.');
  else {
    tail = `${st.state === 'error' ? `${cue('error', 'not added', st.msg)} <span class="text-state-warn">${esc(st.msg)}</span> ` : cue('human', 'asks you', 'Nothing is written until you press Add.')}
      <button type="button" data-jp-add class="cursor-pointer rounded-sm border border-accent bg-transparent px-[8px] py-[1px] text-caveat text-accent-ink">Add</button>
      <button type="button" data-jp-no class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-[8px] py-[1px] text-caveat text-ink">Not now</button>`;
  }
  return `<div data-journal-proposal-box class="mb-s3 flex flex-wrap items-baseline gap-s2 text-caveat text-ink-muted">${head} ${tail}</div>`;
}

function redrawJournalProposal(turn) {
  const box = document.querySelector('#chat-turn-footer [data-journal-proposal-box]');
  if (!box) return;
  box.outerHTML = journalProposalHtml(turn);
  wireJournalProposal(turn);
}

function wireJournalProposal(turn) {
  const box = document.querySelector('#chat-turn-footer [data-journal-proposal-box]');
  box?.querySelector('[data-jp-no]')?.addEventListener('click', () => { turn.jpState = { state: 'dismissed' }; redrawJournalProposal(turn); });
  box?.querySelector('[data-jp-add]')?.addEventListener('click', async () => {
    const p = turn.journalProposal;
    turn.jpState = { state: 'saving' }; redrawJournalProposal(turn);
    try {
      await writeJournalTurn(p.investigation, { sessionId: sessionId(), exchangeId: p.exchange_id, note: '' }, { confirm: true });
      turn.jpState = { state: 'saved' };
      const earlier = state.chat.find((t) => t.exchangeId != null && t.exchangeId === p.exchange_id);
      if (earlier) earlier.journalSaved = true;
    } catch (err) {
      turn.jpState = { state: 'error', msg: err.loginRequired ? 'Sign in to write in the journal.' : err.message };
    }
    redrawJournalProposal(turn);
  });
}

/** A state cue for this file's two grounds: the glyph from the one glyph table plus a short word, the full
 *  sentence on hover. The tone class is a literal in each branch (no class interpolation), so the CSS build
 *  sees every one. `ground` is 'paper' (the pane) or 'chrome' (the dark rail). */
function cue(stateKey, word, title = '', ground = 'paper') {
  const e = stateEntry(stateKey);
  const t = e.tone;
  const open = ground === 'chrome'
    ? (t === 'text-state-ok' ? '<span class="text-state-ok-on-dark"' : t === 'text-state-warn' ? '<span class="text-state-warn-on-dark"' : '<span class="text-chrome-muted"')
    : (t === 'text-state-ok' ? '<span class="text-state-ok"' : t === 'text-state-warn' ? '<span class="text-state-warn"' : '<span class="text-ink-muted"');
  return `${open} data-cue="${esc(stateKey)}" title="${esc(title || e.word)}"><span class="font-glyph" aria-hidden="true">${e.glyph}</span> ${esc(word)}</span>`;
}

/** Run one analysis and say how it ended, from the activity row the run wrote (never from the branch taken
 *  to get there). Shared by the compile's gap list in the rail and the chat answer's own run buttons, so
 *  there is one way to run an analysis from chat. */
async function runAnalysisFlow(slug, entityType, id, onTick = () => {}) {
  try {
    const started = await runAnalysis(slug, id, entityType);
    if (!started || !started.activity_id) {
      return started && started.status === 'skipped'
        ? { state: 'skipped', msg: started.detail || 'Already up to date.' }
        : { state: 'failed', msg: 'The run did not start.' };
    }
    const finished = await pollActivity(started.activity_id, { onTick });
    const failure = activityFailure(finished);
    return failure ? { state: 'failed', msg: failureText(failure) } : { state: 'ran' };
  } catch (err) {
    return { state: 'failed', msg: err.name === 'PollTimeout'
      ? 'Still running after five minutes: stopped watching here. The run itself has not failed.'
      : (err.status === 401 ? 'Sign in to run an analysis.' : err.message) };
  }
}

/** The analyses a compile says would answer the question and have no stored result. `null` when the compile
 *  did not report gaps at all, which is not the same as none. */
function compileGaps(compiled) {
  const g = compiled && compiled.manifest && compiled.manifest.gaps;
  return Array.isArray(g) ? g : null;
}

/** "What would answer this", asked BEFORE asking: the same compile the Ask path runs, for the question in the
 *  box, shown in the rail with its gaps and a run button per gap. Nothing is asked and no turn is added. */
async function previewCompile() {
  const q = ($('ask-input')?.value || '').trim();
  const slug = state.selectedSlug;
  ensureRailShowing();
  railClaim();
  if (!q) {
    railFrame('Compile', slug || '—', `<div data-compile-note class="text-chip text-chrome-muted">Type the question first, then ask what would answer it.</div>`, { sub: 'nothing asked' });
    return;
  }
  if (!slug) {
    railFrame('Compile', '—', `<div data-compile-note class="text-chip text-chrome-muted">Select a resource first: a compile reads one resource's stored results.</div>`, { sub: 'no resource' });
    return;
  }
  const turn = { question: q, slug, entityType: apiEntityType(state.resourceType), compiled: null, pre: true };
  const view = compileView = { turn, compiled: null, rechecked: false, gapRuns: {} };
  railFrame('Compile', slug, `<div class="text-chip">${cue('running', 'reading what would answer this', '', 'chrome')}</div>`, { sub: 'not asked' });
  try {
    view.compiled = await compileContext({ resourceSlug: slug, question: q, entityType: turn.entityType, perspectives: state.activePerspectives });
  } catch (err) {
    if (compileView === view) {
      railFrame('Compile', slug, `<div data-compile-note class="text-chip">${cue('error', 'could not compile', err.message, 'chrome')} <span class="text-chrome-muted">${esc(err.message)}</span></div>`, { sub: 'not asked' });
    }
    return;
  }
  if (compileView !== view) return;
  turn.compiled = view.compiled;
  drawCompile();
}

/** Render the compile behind an answer — ASSESSMENT-CHAT.md §3(a), "open the
 *  compile". `turn.compiled` is the SAME {text, manifest, derivation} shape
 *  `POST /api/context/compile` returns (query.py's `_compiled_payload`),
 *  kept on the turn in full since 2026-09-17 rather than reduced to just
 *  `compileId` — the id told you a compile happened; this is what it
 *  actually put in front of the model.
 *
 *  Opens in the rail's evidence slot, same as `openMembers`/`showEvidence` —
 *  one slot, one ticket, per app.js's own rule for that slot. */
function openCompile(turn) {
  ensureRailShowing();
  railClaim();
  compileView = { turn, compiled: turn.compiled || null, rechecked: false, gapRuns: {} };
  if (!compileView.compiled) {
    railFrame('Compile', turn.slug || '—',
      `<div class="text-chip text-chrome-muted">${turn.factLayer
        ? 'This answer came from the stored results directly, with no compile behind it.'
        : 'This answer carries no compile — it was answered from retrieval, or from a session with no resource in scope.'}</div>`,
      { sub: 'no compile' });
    return;
  }
  drawCompile();
}

let compileView = null;   // { turn, compiled, rechecked, gapRuns: {analysis id: {state, msg}} }

/** A gap's ONE state, from what the compile judged it (`state`, `reason`, `explanation`: context_compile._judge_gap,
 *  which reads the fact layer's run rows), never assumed. "not run" only for a gap judged never_run; a gap judged
 *  nothing else is said in the compile's own words under the ? cue. */
const GAP_CUE = { never_run: 'unrun', nothing_found: 'nothing', partial: 'partial', not_established: 'not_established', no_reader: 'no_reader' };
function gapCue(g, ground) {
  const key = GAP_CUE[g && g.state] || 'unknown';
  const word = g && g.state === 'never_run' ? 'not run' : (g && g.reason) || 'not judged';
  return cue(key, word, (g && (g.explanation || g.reason)) || 'The compile did not say why this has no stored result.', ground);
}

function gapRowHtml(g, k) {
  const run = compileView.gapRuns[g.key] || { state: 'idle' };
  const status = run.state === 'running' ? cue('running', 'running', '', 'chrome')
    : run.state === 'failed' ? `${cue('error', 'failed', run.msg, 'chrome')} <span class="text-chrome-muted">${esc(run.msg)}</span>`
    : run.state === 'skipped' ? cue('measured', 'already up to date', run.msg, 'chrome')
    : run.state === 'ran' ? cue('partial', 'ran, still no stored result', 'The run finished and the compile still finds no stored result for it.', 'chrome')
    : gapCue(g, 'chrome');
  return `<div data-gap="${esc(g.key)}" class="mb-[3px] flex flex-wrap items-baseline gap-s2">
    <span class="font-mono text-chrome-ink">${esc(g.key)}</span>
    <span data-gap-status>${status}</span>
    <button data-gap-run="${k}" ${run.state === 'running' ? 'disabled' : ''}
      class="cursor-pointer rounded-sm border border-accent bg-transparent px-[6px] py-[1px] text-caps text-accent-on-dark disabled:cursor-default disabled:opacity-60"
      >${run.state === 'running' ? 'Running…' : run.state === 'failed' ? 'Run again' : 'Run'}</button>
  </div>`;
}

/** The gap list's count, said as "N not run" only when every gap was judged never_run. */
function gapsHeadCue(gaps) {
  const notRun = gaps.filter((g) => g && g.state === 'never_run').length;
  return notRun === gaps.length
    ? cue('unrun', `${gaps.length} not run`, 'Analyses that have not run on this resource.', 'chrome')
    : cue('not_established', `${gaps.length} with no stored result${notRun ? ` · ${notRun} not run` : ''}`,
      'Analyses with no stored result on this resource; each row says why.', 'chrome');
}

/** The compile panel's narratives block: each passage the answer could cite, with its citation and "proposal"; a
 *  section that was dropped, not read, unmatched or unreachable says which, in words. */
export function narrativesHtml(manifest) {
  return narrativeSections(manifest).map((n) => {
    const rows = n.passages.map((p) => `<li data-narrative-passage="${esc(p.passage_id || '')}"><span>${esc(p.citation || p.stated_by || p.file_path || '')}</span>
      <span class="text-chrome-muted"> · </span><span data-narrative-label>proposal</span></li>`).join('');
    return `<div data-compile-narratives data-narrative-state="${esc(n.state)}${n.state === 'found' && !n.packed ? '-dropped' : ''}" class="mb-s2 text-chip">
      <div>${esc(narrativeWords(n))}</div>${rows ? `<ul class="mt-s1 list-disc pl-s3">${rows}</ul>` : ''}</div>`;
  }).join('');
}

function evidenceStateHtml() {
  const { compiled, gapRuns } = compileView;
  const m = compiled.manifest && typeof compiled.manifest === 'object' ? compiled.manifest : {};
  const gaps = compileGaps(compiled);
  const deferredKeys = (Array.isArray(m.deferred) ? m.deferred : []).map((x) => x && x.key).filter(Boolean);
  const packed = Array.isArray(m.packed) ? m.packed : null;
  const storedLine = packed
    ? `<div data-compile-stored class="mb-s1 text-chip"><span class="tnum">${packed.length}</span> stored result${packed.length === 1 ? '' : 's'} used${
        packed.length ? `: <span class="font-mono">${packed.map((x) => esc(packedLabel(x))).join(', ')}</span>` : ''}</div>`
    : `<div data-compile-stored class="mb-s1 text-chip">${cue('not_established', 'stored results used: not reported', 'This compile did not say which stored results it used.', 'chrome')}</div>`;
  // An analysis that was run from here and now has a stored result: said once, from the fresh compile.
  const nowStored = Object.entries(gapRuns).filter(([key, r]) => r.state === 'ran' && !(gaps || []).some((g) => g.key === key)).map(([key]) => key);
  const gapBlock = gaps === null
    ? `<div data-compile-gaps class="mb-s2 text-chip">${cue('not_established', 'gaps not reported', 'This compile did not say which analyses are missing, which is not the same as none missing.', 'chrome')}</div>`
    : gaps.length === 0
      ? (deferredKeys.length
        // Ranked below the section cap: never read, so neither stored nor missing. Said, not folded into "nothing missing".
        ? `<div data-compile-gaps data-compile-deferred class="mb-s2 text-chip">${cue('partial', `${deferredKeys.length} not read`, 'These ranked below the compile\'s section cap, so the compile did not look for a stored result.', 'chrome')} <span class="text-chrome-muted">nothing missing among the ones read; not read, below the section cap:</span> <span class="font-mono">${deferredKeys.map(esc).join(', ')}</span></div>`
        : `<div data-compile-gaps class="mb-s2 text-chip">${cue('measured', 'nothing missing', 'Every analysis that answers this has a stored result.', 'chrome')} <span class="text-chrome-muted">every analysis that answers this has a stored result</span></div>`)
      : `<div data-compile-gaps class="mb-s2 border border-chrome-line p-s2 text-chip">
          <div class="mb-s1">${gapsHeadCue(gaps)} <span class="text-chrome-muted">would answer this</span></div>
          ${gaps.map(gapRowHtml).join('')}
        </div>`;
  const ranLine = nowStored.length
    ? `<div data-compile-ran class="mb-s2 text-chip">${nowStored.map((k) => `${cue('measured', 'ran', '', 'chrome')} <span class="font-mono">${esc(k)}</span> <span class="text-chrome-muted">now has a stored result</span>`).join(' · ')}</div>`
    : '';
  return storedLine + narrativesHtml(m) + gapBlock + ranLine;
}

/** One manifest entry as a row. A plain value prints as is; a list prints its length; an OBJECT (`lists`,
 *  `coverage`, ...) prints a short summary (how many entries, and the first few names) and keeps the whole
 *  thing on demand under "details". Never `String(object)`, which is "[object Object]". */
export function manifestValueSummary(v) {
  if (Array.isArray(v)) return `${v.length} item(s)`;
  if (v && typeof v === 'object') {
    const keys = Object.keys(v);
    const names = keys.slice(0, 3).join(', ');
    return `${keys.length} ${keys.length === 1 ? 'entry' : 'entries'}${keys.length ? `: ${names}${keys.length > 3 ? ', …' : ''}` : ''}`;
  }
  return String(v);
}

export function manifestRowHtml(k, v) {
  const isObj = v && typeof v === 'object' && !Array.isArray(v);
  return `<div data-manifest-row="${esc(k)}" class="mb-[3px]"><span class="font-mono text-accent-on-dark">${esc(k)}</span>
        <span class="text-chrome-muted"> · </span>${esc(manifestValueSummary(v))}${isObj
    ? ` <details data-manifest-detail class="ml-s2 inline-block align-top"><summary class="cursor-pointer text-chrome-muted">details</summary>
        <pre class="mt-s1 max-h-[30vh] overflow-auto whitespace-pre-wrap text-chip text-chrome-ink">${esc(JSON.stringify(v, null, 2))}</pre></details>` : ''}</div>`;
}

function drawCompile() {
  const { turn, compiled, rechecked } = compileView;
  const forWhat = turn.slug || '—';
  const manifest = compiled.manifest && typeof compiled.manifest === 'object' ? compiled.manifest : {};
  const manifestRows = Object.entries(manifest).filter(([k, v]) => k !== 'gaps' && k !== 'packed' && k !== 'narratives' && (
    Array.isArray(v) ? v.length : v != null && v !== ''));
  const manifestHtml = manifestRows.length
    ? manifestRows.map(([k, v]) => manifestRowHtml(k, v)).join('')
    : `<div class="text-chip text-chrome-muted">The manifest carried no other populated keys.</div>`;
  const text = String(compiled.text || '');
  const derivation = compiled.derivation;
  const body = railFrame('Compile', forWhat, `
    <div class="mb-s2 text-caps text-chrome-muted">${turn.pre
      ? `What would answer “${esc(turn.question)}”. Nothing has been asked.`
      : `What the model was shown to answer “${esc(turn.question)}”.`}${rechecked ? ' Re-checked just now.' : ''}</div>
    ${evidenceStateHtml()}
    <div class="mb-s3">${manifestHtml}</div>
    ${text ? `<details class="mb-s3">
      <summary class="cursor-pointer text-caps text-accent-on-dark">the compiled text · <span class="tnum">${text.length}</span> characters</summary>
      <pre class="mt-s2 max-h-[40vh] overflow-auto whitespace-pre-wrap text-chip text-chrome-ink">${esc(text)}</pre>
    </details>` : ''}
    ${derivation ? `<details>
      <summary class="cursor-pointer text-caps text-accent-on-dark">derivation</summary>
      <pre class="mt-s2 max-h-[30vh] overflow-auto whitespace-pre-wrap text-chip text-chrome-ink">${esc(JSON.stringify(derivation, null, 2))}</pre>
    </details>` : ''}`,
    { sub: turn.pre ? 'before asking' : 'the strongest provenance object in this codebase' });
  const gaps = compileGaps(compiled) || [];
  body?.querySelectorAll('[data-gap-run]').forEach((b) => b.addEventListener('click', () => runGap(gaps[Number(b.dataset.gapRun)])));
}

/** Run one missing analysis from the compile's gap list, then compile again and draw THAT: the gap is gone
 *  only if the new compile no longer finds it missing. */
async function runGap(gap) {
  const view = compileView;
  if (!view || !gap || (view.gapRuns[gap.key] || {}).state === 'running') return;
  const { turn } = view;
  view.gapRuns[gap.key] = { state: 'running' };
  drawCompile();
  const out = await runAnalysisFlow(turn.slug, turn.entityType, gap.key);
  if (compileView !== view) return;
  view.gapRuns[gap.key] = out;
  if (out.state === 'ran') {
    try {
      view.compiled = await compileContext({ resourceSlug: turn.slug, question: turn.question, entityType: turn.entityType, perspectives: state.activePerspectives });
      view.rechecked = true;
    } catch (err) {
      view.gapRuns[gap.key] = { state: 'failed', msg: `ran, but the compile could not be re-read: ${err.message}` };
    }
  }
  if (compileView === view) drawCompile();
}

/**
 * Promote a chat turn into the content pane — the move
 * ASSESSMENT-CHAT.md §2 and SPEC-THE-STAGE-PAGE.md both call for: the answer,
 * whatever form it takes, at full width, with the rail left holding only the
 * turn list and provenance.
 *
 * `promoteToPane()` (app.js, shared with the Questions-checklist's own
 * row-promotion path) renders the artefact itself — prose, Plotly chart, or
 * a Kroki-rendered diagram. This wraps it and appends what is chat-specific:
 * the list sentences an answer was compiled from, the source line, the
 * "open the compile" link, the vote bar, and copy-as-evidence — the same
 * footer the old rail-bound `renderChatLog()` built, moved to sit under the
 * answer instead of squeezed beside it.
 */
export async function promoteChatTurn(turn) {
  await promoteToPane(turn);
  // `promoteToPane()` (app.js) only knows how to render an ARTEFACT — it has
  // no notion of "the question failed". A turn that errored still gets
  // opened here (submitAsk always promotes, so the person is not left
  // staring at a stale earlier answer) and needs its own error text, not
  // whatever the generic renderer made of an empty `turn.answer`.
  if (turn.error) {
    const body = $('promoted-body');
    if (body) body.innerHTML = `<div class="text-answer text-state-warn">${esc(turn.error)}</div>`;
  }
  const i = state.chat.indexOf(turn);
  renderPromotedFooter(turn, i);
  renderTurnList();
}

/**
 * The stream's structured `done` payloads — `symbol_table` (code_inventory),
 * `compare_symbols` (a comparison about API surface) and `alias_suggestion`
 * (no resource resolved, but a fuzzy one exists). None of these existed on
 * the non-streaming `POST /api/query/` response before item 9 wired the
 * stream in (ASSESSMENT-CHAT.md §3(b)) — the blocking path never asked for
 * them. A small table each, in the pane rather than the rail, same rule as
 * everything else here: a table is a row shape, not a provenance line.
 */
function structuredTableHtml(turn) {
  const out = [];
  if (turn.symbolTable && Array.isArray(turn.symbolTable.items) && turn.symbolTable.items.length) {
    const t = turn.symbolTable;
    out.push(`<div class="mb-s3">
      <div class="mb-s1 text-caps text-ink-muted">${t.kind} symbols in <span class="font-mono">${esc(t.project)}</span>
        — <span class="tnum">${t.items.length}</span> of <span class="tnum">${t.total}</span> shown</div>
      <table class="w-full border-collapse text-caveat"><tbody>
        ${t.items.map((r) => `<tr class="border-b border-rule">
          <td class="py-[3px] pr-s2 align-top text-ink-muted">${esc(r.kind)}</td>
          <td class="py-[3px] pr-s2 align-top font-mono">${esc(r.name)}</td>
          <td class="py-[3px] align-top text-ink-muted">${esc(r.file)}:${esc(String(r.line ?? ''))}</td>
        </tr>`).join('')}
      </tbody></table></div>`);
  }
  if (turn.compareSymbols && Array.isArray(turn.compareSymbols.sides) && turn.compareSymbols.sides.length) {
    const c = turn.compareSymbols;
    out.push(`<div class="mb-s3">
      <div class="mb-s1 text-caps text-ink-muted">${c.kind} comparison</div>
      <div class="grid grid-cols-2 gap-s3">
        ${c.sides.map((s) => `<div>
          <div class="mb-[2px] font-mono text-caveat text-ink">${esc(s.slug)} · <span class="tnum">${s.total}</span></div>
          ${s.items.slice(0, 10).map((r) => `<div class="text-caveat text-ink-muted">${esc(r.kind)} · ${esc(r.name)}</div>`).join('')}
        </div>`).join('')}
      </div></div>`);
  }
  if (turn.journalProposal) out.push(journalProposalHtml(turn));
  if (turn.resourceQuestion) out.push(otherResourceHtml(turn));
  if (turn.aliasSuggestion) out.push(aliasHtml(turn));
  if (turn.resourceCheckError) {
    out.push(`<div class="mb-s3 flex flex-wrap items-baseline gap-s2 text-caveat text-ink-muted">${cue('error', 'not checked', turn.resourceCheckError)}
      <span>could not check whether the question names another resource</span></div>`);
  }
  return out.join('');
}

/* ── an alias suggestion: confirm it (PI-119) ─────────────────────────────
 * "No resource named X was resolved: did you mean Y?" used to be a sentence. Now it can be answered. The
 * saved state is read back from the alias list the server holds (not assumed from the POST), and the
 * server stores the alias normalised, so the read-back compares the normalised form. */
const normaliseAlias = (t) => String(t).toLowerCase().replace(/ /g, '_').replace(/-/g, '_');

function aliasHtml(turn) {
  const a = turn.aliasSuggestion;
  const st = turn.aliasState || { state: 'asking' };
  const head = `<span data-alias-sentence>No resource named “${esc(a.term)}” was resolved: did you mean
    <span class="font-mono text-ink">${esc(a.candidate_name)}</span> (<span class="font-mono">${esc(a.candidate_slug)}</span>)?</span>`;
  let tail;
  if (st.state === 'saved') {
    tail = `${cue('measured', 'remembered', 'Saved, and read back from the alias list.')} <span data-alias-saved>“${esc(a.term)}” will resolve to ${esc(a.candidate_slug)} in future questions.</span>${turn.held ? ` ${cue('running', 'asking', 'Asking the question again on the resource you chose.')}` : ''}`;
  } else if (st.state === 'conflict') {
    tail = `${cue('partial', `already used for ${st.other}`, 'This name already points at another resource. Moving it changes what it means in every future question.')}
      <button type="button" data-alias-move class="cursor-pointer rounded-sm border border-accent bg-transparent px-[8px] py-[1px] text-caveat text-accent-ink">Move it here</button>
      <button type="button" data-alias-no class="cursor-pointer bg-transparent text-caveat text-ink-muted underline">Leave it</button>`;
  } else if (st.state === 'declined') {
    tail = `${cue('optional', 'not remembered', 'You said no. Nothing was saved.')} <span>${turn.held ? 'asked on the current resource' : 'asked again next time'}</span>`;
  } else if (st.state === 'saving') {
    tail = cue('running', 'saving');
  } else if (st.state === 'once') {
    tail = cue('running', 'asking', `Asking the question again on ${a.candidate_name}. Nothing was saved.`);
  } else {
    tail = `${st.state === 'error' ? `${cue('error', 'not saved', st.msg)} <span class="text-state-warn">${esc(st.msg)}</span> ` : cue('human', 'asks you', 'Remember this name for this resource?')}
      <button type="button" data-alias-yes class="cursor-pointer rounded-sm border border-accent bg-transparent px-[8px] py-[1px] text-caveat text-accent-ink">Yes, remember it</button>
      <button type="button" data-alias-once class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-[8px] py-[1px] text-caveat text-ink">Ask about ${esc(a.candidate_name)} once</button>
      <button type="button" data-alias-no class="cursor-pointer bg-transparent text-caveat text-ink-muted underline">No</button>`;
  }
  return `<div data-alias-box class="mb-s3 flex flex-wrap items-baseline gap-s2 text-caveat text-ink-muted">${head} ${tail}</div>`;
}

async function confirmAliasSuggestion(turn, { move = false } = {}) {
  const a = turn.aliasSuggestion;
  if (!a || (turn.aliasState || {}).state === 'saving') return;
  turn.aliasState = { state: 'saving' };
  redrawAlias(turn);
  try {
    await addAlias(a.term, a.candidate_slug, { move });
    const held = await listAliases(a.candidate_slug);
    const want = normaliseAlias(a.term);
    const found = (held.aliases || []).some((x) => x.alias === want && x.project_slug === a.candidate_slug);
    turn.aliasState = found ? { state: 'saved' } : { state: 'error', msg: 'The save went through but the alias is not in the list the server holds.' };
    if (found && turn.held) { redrawAlias(turn); await askAgainOn(turn, a.candidate_slug, a.candidate_type); return; }
  } catch (err) {
    const held = err.status === 409 ? /already used for (.+)$/.exec(err.message) : null;
    if (held) { turn.aliasState = { state: 'conflict', other: held[1] }; redrawAlias(turn); return; }
    turn.aliasState = { state: 'error', msg: err.status === 401 ? 'Sign in to save an alias.' : err.message };
  }
  redrawAlias(turn);
}

function redrawAlias(turn) {
  const box = document.querySelector('#chat-turn-footer [data-alias-box]');
  if (!box) return;
  box.outerHTML = aliasHtml(turn);
  wireAlias(turn);
}

function wireAlias(turn) {
  const box = document.querySelector('#chat-turn-footer [data-alias-box]');
  box?.querySelector('[data-alias-yes]')?.addEventListener('click', () => confirmAliasSuggestion(turn));
  box?.querySelector('[data-alias-move]')?.addEventListener('click', () => confirmAliasSuggestion(turn, { move: true }));
  box?.querySelector('[data-alias-once]')?.addEventListener('click', async () => {
    const a = turn.aliasSuggestion;
    turn.aliasState = { state: 'once' }; redrawAlias(turn);
    await askAgainOn(turn, a.candidate_slug, a.candidate_type);
  });
  box?.querySelector('[data-alias-no]')?.addEventListener('click', async () => {
    turn.aliasState = { state: 'declined' }; redrawAlias(turn);
    if (turn.held) await askAgainOn(turn, turn.slug, apiToUiType(turn.entityType), { keep: true });
  });
}

/* ── a scoped question that names ANOTHER resource (owner ruling 2026-10-13) ──
 * The server held the turn: no answer was composed for the wrong resource. Chat stays scoped, so the
 * person chooses — ask about the named one (switch, then ask again), or keep this one (ask again as
 * written, with the check skipped). The switch is read back from the app state before the question is
 * re-asked, never assumed. */
const apiToUiType = (t) => (t === 'database' ? 'db' : (t || 'repo'));

/** Re-ask `turn.question` on `slug`. Switches the selection first (and says so if it did not take);
 *  `keep` re-asks on the current scope with the name check skipped. */
async function askAgainOn(turn, slug, type, { keep = false } = {}) {
  if (!keep) {
    const uiType = apiToUiType(type);
    const switched = await selectResourceFromChat(uiType, slug);
    if (!switched) {
      turn.rqState = { state: 'error', msg: `Could not switch to ${slug}.` };
      redrawResourceQuestion(turn);
      return;
    }
  }
  await submitAsk({ question: turn.question, skipResourceCheck: true });
}

function otherResourceHtml(turn) {
  const r = turn.resourceQuestion;
  const st = turn.rqState || { state: 'asking' };
  const head = `<span data-rq-sentence>You asked about <span class="font-mono text-ink">${esc(r.named_name)}</span>; this chat is on <span class="font-mono">${esc(r.scoped_slug)}</span>.</span>`;
  let tail;
  if (st.state === 'switching') {
    tail = cue('running', 'asking', `Asking the question again on ${r.named_name}.`);
  } else if (st.state === 'kept') {
    tail = cue('optional', `kept ${r.scoped_slug}`, 'Asked again on the resource this chat is on.');
  } else {
    tail = `${st.state === 'error' ? `${cue('error', 'not switched', st.msg)} ` : cue('human', 'asks you', 'Nothing was answered yet: the question names a different resource than this chat is on.')}
      <button type="button" data-rq-switch class="cursor-pointer rounded-sm border border-accent bg-transparent px-[8px] py-[1px] text-caveat text-accent-ink">Ask about ${esc(r.named_name)}</button>
      <button type="button" data-rq-keep class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-[8px] py-[1px] text-caveat text-ink">Keep ${esc(r.scoped_slug)}</button>`;
  }
  return `<div data-resource-question-box class="mb-s3 flex flex-wrap items-baseline gap-s2 text-caveat text-ink-muted">${head} ${tail}</div>`;
}

function redrawResourceQuestion(turn) {
  const box = document.querySelector('#chat-turn-footer [data-resource-question-box]');
  if (!box) return;
  box.outerHTML = otherResourceHtml(turn);
  wireResourceQuestion(turn);
}

function wireResourceQuestion(turn) {
  const box = document.querySelector('#chat-turn-footer [data-resource-question-box]');
  box?.querySelector('[data-rq-switch]')?.addEventListener('click', async () => {
    const r = turn.resourceQuestion;
    turn.rqState = { state: 'switching' }; redrawResourceQuestion(turn);
    await askAgainOn(turn, r.named_slug, r.named_type);
  });
  box?.querySelector('[data-rq-keep]')?.addEventListener('click', async () => {
    turn.rqState = { state: 'kept' }; redrawResourceQuestion(turn);
    await askAgainOn(turn, turn.slug, turn.entityType, { keep: true });
  });
}

/* ── the analyses behind a chat answer: run one, or schedule it (PI-118) ──
 * `turn.runnable` is [{id, ran, run: {state, msg}, sched: {state, cadence, enabled}}]. For a question row
 * it is the analyses that answer that question; for a free-text question it is the gaps in the compile the
 * answer was composed from. The schedule line is read from the schedules the server holds for the resource,
 * never assumed from the save. */
const CADENCES = ['daily', 'weekly', 'monthly'];

function runnableRowHtml(r, k) {
  const run = r.run || { state: 'idle' };
  const status = run.state === 'running' ? cue('running', 'running', 'The run is in progress; this reads its activity row.')
    : run.state === 'failed' ? `${cue('error', 'failed', run.msg)} <span class="text-state-warn">${esc(run.msg)}</span>`
    : run.state === 'skipped' ? cue('measured', 'already up to date', run.msg)
    : run.state === 'ran' ? cue('measured', 'ran just now', 'The run finished without an error.')
    : r.ran ? cue('measured', 'has run', 'This analysis has a recorded run for this resource.')
    : r.gap ? gapCue(r.gap)
    : cue('unrun', 'not run', 'No stored result for this analysis on this resource.');
  const sc = r.sched || { state: 'unread' };
  const sched = sc.state === 'set'
    ? cue('measured', `scheduled ${sc.cadence}${sc.enabled === false ? ' · paused' : ''}`, 'Read from the schedules held for this resource.')
    : sc.state === 'none' ? cue('optional', 'not scheduled', 'The server holds no schedule for this analysis on this resource.')
    : sc.state === 'error' ? `${cue(sc.what === 'saved' ? 'error' : 'unknown', sc.what === 'saved' ? 'schedule not saved' : 'schedule not read', sc.msg)} <span class="text-state-warn">${esc(sc.msg)}</span>`
    : cue('unknown', 'schedule not read yet', 'The schedules for this resource have not been read.');
  const saving = sc.saving;
  return `<div data-runnable="${esc(r.id)}" class="mb-s1 flex flex-wrap items-baseline gap-s2 text-caveat">
    <span class="w-[14rem] shrink-0 truncate font-mono text-ink" title="${esc(r.id)}">${esc(r.id)}</span>
    <span data-run-status>${status}</span>
    <button type="button" data-run="${k}" ${run.state === 'running' ? 'disabled' : ''}
      class="cursor-pointer rounded-sm border border-accent bg-transparent px-[8px] py-[1px] text-caveat text-accent-ink disabled:cursor-default disabled:opacity-60"
      >${run.state === 'running' ? 'Running…' : r.ran || run.state === 'ran' ? 'Run again' : 'Run'}</button>
    <span data-sched-status>${sched}</span>
    <select data-sched-cadence="${k}" aria-label="schedule for ${esc(r.id)}"
      class="rounded-sm border border-rule bg-transparent px-1 py-[1px] text-caveat text-ink">${CADENCES.map((c) =>
        `<option value="${c}" ${sc.cadence === c ? 'selected' : ''}>${c}</option>`).join('')}</select>
    <button type="button" data-sched-save="${k}" ${saving ? 'disabled' : ''}
      class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-[8px] py-[1px] text-caveat text-accent-ink disabled:cursor-default disabled:opacity-60"
      >${saving ? 'Saving…' : sc.state === 'set' ? 'Change schedule' : 'Schedule'}</button>
  </div>`;
}

function runnableBoxHtml(turn) {
  const rows = turn.runnable || [];
  if (!rows.length) return '';
  return `<div data-runnable-box class="mt-s3 border-t border-rule pt-s2">
    <div class="mb-s1 text-caps uppercase tracking-caps text-ink-muted">${turn.factLayer ? 'Analyses behind this answer' : 'Analyses that have not run here'}</div>
    ${rows.map(runnableRowHtml).join('')}
    ${turn.rechecked ? `<div data-recheck-note class="mt-s1 text-caveat text-ink-muted">${turn.rechecked}</div>` : ''}
  </div>`;
}

function redrawRunnable(turn) {
  const box = document.querySelector('#chat-turn-footer [data-runnable-box]');
  if (!box) return;
  box.outerHTML = runnableBoxHtml(turn);
  wireRunnable(turn);
}

function wireRunnable(turn) {
  const box = document.querySelector('#chat-turn-footer [data-runnable-box]');
  if (!box) return;
  box.querySelectorAll('[data-run]').forEach((b) => b.addEventListener('click', () => runRunnable(turn, Number(b.dataset.run))));
  box.querySelectorAll('[data-sched-save]').forEach((b) => b.addEventListener('click', () => {
    const k = Number(b.dataset.schedSave);
    const sel = box.querySelector(`[data-sched-cadence="${k}"]`);
    scheduleRunnable(turn, k, sel ? sel.value : 'daily');
  }));
}

/** Read the schedules the server holds for this resource into every runnable row (once per footer draw). */
async function loadRunnableSchedules(turn) {
  if (!turn.slug || !(turn.runnable || []).length) return;
  let rows = null; let msg = '';
  try { rows = await getSchedules(turn.entityType, turn.slug); } catch (err) { msg = err.message; }
  for (const r of turn.runnable) {
    if (rows === null) { r.sched = { state: 'error', msg }; continue; }
    const mine = (Array.isArray(rows) ? rows : []).find((x) => x.analysis_id === r.id);
    r.sched = mine ? { state: 'set', cadence: mine.schedule, enabled: mine.enabled !== false && mine.enabled !== 0 } : { state: 'none' };
  }
  if (state.promoted === turn) redrawRunnable(turn);
}

async function scheduleRunnable(turn, k, cadence) {
  const r = (turn.runnable || [])[k];
  if (!r || (r.sched || {}).saving) return;
  r.sched = { ...(r.sched || {}), saving: true };
  redrawRunnable(turn);
  try {
    await saveSchedule(turn.entityType, turn.slug, r.id, cadence, true);
  } catch (err) {
    r.sched = { state: 'error', what: 'saved', msg: err.status === 401 ? 'Sign in to schedule an analysis.' : err.message };
    redrawRunnable(turn);
    return;
  }
  await loadRunnableSchedules(turn);
}

async function runRunnable(turn, k) {
  const r = (turn.runnable || [])[k];
  if (!r || (r.run || {}).state === 'running') return;
  r.run = { state: 'running' };
  redrawRunnable(turn);
  const out = await runAnalysisFlow(turn.slug, turn.entityType, r.id);
  r.run = out;
  if (out.state !== 'ran') { redrawRunnable(turn); return; }
  await refreshAfterRun(turn, r);
}

/** After a run: the answer is read again from where it came from. A question row reads its own answer
 *  endpoint again; a free-text question compiles again and lists what is STILL missing. */
async function refreshAfterRun(turn, ran) {
  const keep = new Map((turn.runnable || []).map((x) => [x.id, x]));
  try {
    if (turn.refresh) {
      const next = await turn.refresh();
      turn.answer = next.answer;
      turn.source = next.source;
      turn.runnable = (next.runnable || []).map((x) => ({ ...x, run: (keep.get(x.id) || {}).run || { state: 'idle' }, sched: (keep.get(x.id) || {}).sched || { state: 'unread' } }));
      turn.rechecked = '';
      await promoteChatTurn(turn);
      return;
    }
    const fresh = await compileContext({ resourceSlug: turn.slug, question: turn.question, entityType: turn.entityType, perspectives: state.activePerspectives });
    const gaps = compileGaps(fresh) || [];
    const stillMissing = gaps.map((g) => ({ id: g.key, ran: false, gap: g, run: (keep.get(g.key) || {}).run || { state: 'idle' }, sched: (keep.get(g.key) || {}).sched || { state: 'unread' } }));
    const done = [...keep.values()].filter((x) => x.run && x.run.state === 'ran' && !gaps.some((g) => g.key === x.id));
    turn.runnable = [...done, ...stillMissing];
    turn.rechecked = done.length
      ? `Ran ${done.map((x) => x.id).join(', ')}; it now has a stored result. This answer was written before it ran, so ask again to use it.` : '';
  } catch (err) {
    ran.run = { state: 'failed', msg: `ran, but the answer could not be re-read: ${err.message}` };
  }
  redrawRunnable(turn);
}

/** The analyses a compile lists as missing, as run/schedule rows. */
function gapRunnable(compiled) {
  return (compileGaps(compiled) || []).map((g) => ({ id: g.key, ran: false, gap: g, run: { state: 'idle' }, sched: { state: 'unread' } }));
}

/** "Ask in chat" on a question row (PI-055): a turn whose answer is the row's own answer text, with the analyses
 *  behind it to run or schedule. `refresh` reads the answer again (the row's own function) after a run. */
export async function openQuestionTurn({ question, slug, entityType, answer, source, runnable = [], refresh }) {
  const turn = {
    question, slug, entityType, pending: false, streaming: false, answer, source, intent: '',
    factLayer: true, compiled: null, queryHash: '',
    runnable: runnable.map((r) => ({ ...r, run: { state: 'idle' }, sched: { state: 'unread' } })),
    refresh,
  };
  state.chat.push(turn);
  ensureRailShowing();
  renderTurnList();
  await promoteChatTurn(turn);
}

function renderPromotedFooter(turn, i) {
  const body = $('promoted-body');
  if (!body) return;
  let footer = document.getElementById('chat-turn-footer');
  if (!footer) {
    footer = document.createElement('div');
    footer.id = 'chat-turn-footer';
    footer.className = 'mt-s4 border-t border-rule pt-s3';
    body.parentElement.appendChild(footer);
  }
  const said = new Set((turn.lists || []).map((l) => l.key));
  const listButtons = (turn.listSources || []).filter((src) => !said.has(src)).map((src) =>
    `<button data-list-source="${esc(src)}" data-list-slug="${esc(turn.slug || '')}"
      class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-[10px] py-[4px]
             text-caveat text-ink">Open the list · <span class="font-mono">${esc(src)}</span> ›</button>`).join('');

  // The source line itself is NOT repeated here — `promoteToPane()` already
  // prints `turn.source` right under the question, in the pane header. This
  // footer is the rest of the provenance: the compile behind it, the lists
  // it was compiled from, the vote, and a way to copy it. Printing `source`
  // twice is exactly the two-copies-of-one-fact shape SPEC-THE-STAGE-PAGE.md
  // was written to stop.
  footer.innerHTML = `
    ${structuredTableHtml(turn)}
    ${runnableBoxHtml(turn)}
    ${evidenceFooterListsHtml(turn.lists || [], turn.slug || '')}
    ${listButtons ? `<div class="mt-s2 flex flex-wrap gap-s2">${listButtons}</div>` : ''}
    <div class="mt-s3 flex flex-wrap items-baseline gap-s3 text-caveat">
      <button data-act="open-compile"
        class="cursor-pointer bg-transparent text-caveat text-accent-ink underline"
        >${turn.factLayer ? 'the evidence behind this answer' : 'the evidence this answer was composed from'} ›</button>
      <button data-act="copy-turn"
        class="cursor-pointer bg-transparent text-caveat text-ink-muted underline"
        >${icon('copy', { size: 13 })} copy as evidence</button>
      ${journalTriggerHtml(turn)}
      ${turn.intent ? `<span class="text-ink-muted">intent ${esc(turn.intent)}${turn.cached ? ' · cached' : ''}</span>` : ''}
    </div>
    <div data-journal-form class="mt-s2"></div>
    ${turn.queryHash ? `<div class="mt-s2">${feedbackHtml(turn, i)}</div>` : ''}`;

  footer.querySelector('[data-act="journal-turn"]')?.addEventListener('click', () => openJournalForm(turn, footer));
  footer.querySelectorAll('[data-list-source]').forEach((b) => b.addEventListener('click', () => {
    const slug = b.dataset.listSlug || state.selectedSlug;
    if (!slug) return;
    openMembers({ slug, analysisId: b.dataset.listSource, title: b.dataset.listSource.replace(/_/g, ' ') });
  }));
  footer.querySelector('[data-act="open-compile"]')?.addEventListener('click', () => openCompile(turn));
  wireAlias(turn);
  wireResourceQuestion(turn);
  wireJournalProposal(turn);
  wireRunnable(turn);
  // Read the schedules once per turn (the footer redraws on a vote; the rows keep what they read).
  if ((turn.runnable || []).some((r) => (r.sched || {}).state === 'unread')) loadRunnableSchedules(turn);
  footer.querySelector('[data-act="copy-turn"]')?.addEventListener('click', (e) =>
    copyAsEvidence(turnAsMarkdown(turn), e.currentTarget));
  footer.querySelectorAll('[data-vote]').forEach((b) => b.addEventListener('click', () => {
    vote(Number(b.dataset.turn), Number(b.dataset.vote));
  }));
}

/** Fold a `/api/query/` or a stream's terminal `done` event into a turn,
 *  identically either way — the two response shapes agree on every field
 *  this reads except `hash`/`query_hash`, normalised by the caller before
 *  this runs. */
function applyAnswer(turn, body) {
  turn.pending = false;
  turn.streaming = false;
  turn.answer = body.response ?? turn.answer ?? '';
  turn.intent = body.intent || '';
  turn.cached = Boolean(body.cached);
  turn.source = sourceLine(body);
  // Never computed here — the hash always comes off a server response, so
  // a vote lands under the same key however the answer was produced.
  turn.queryHash = body.query_hash || '';
  turn.compileId = body.compiled?.manifest?.compile_id || null;
  // The full compile, not just its id (ASSESSMENT-CHAT.md §3(a)) — kept on
  // the turn so "open the compile" needs no round trip.
  turn.compiled = body.compiled || null;
  turn.listSources = listSources(body);
  turn.lists = listSentences(body);
  // The chart the server chose to attach (statistical/health/comparison
  // intents produce one). A Plotly figure, and far too wide for the rail.
  turn.chart = body.chart || null;
  turn.mermaid = extractMermaid(turn.answer);
  turn.symbolTable = body.symbol_table || null;
  turn.compareSymbols = body.compare_symbols || null;
  turn.aliasSuggestion = body.alias_suggestion || null;
  // A scoped question that named ANOTHER resource: held, not answered (see aliasHtml / otherResourceHtml).
  turn.resourceQuestion = body.resource_question || null;
  turn.held = Boolean(body.held);
  // The journal tool's question: the pane draws [Add] [Not now] for it, so the bracketed words are not left in the text.
  turn.journalProposal = body.journal_proposal || null;
  turn.exchangeId = body.exchange_id ?? null;
  // Turns the chat's journal tool just put in the journal: their per-turn control shows the saved cue.
  for (const id of body.journal_written || []) {
    const earlier = state.chat.find((t) => t.exchangeId != null && t.exchangeId === id);
    if (earlier) earlier.journalSaved = true;
  }
  if (turn.journalProposal) turn.answer = String(turn.answer || '').replace(/\s*\[Add\]\s*\[Not now\]\s*$/, '');
  turn.resourceCheckError = body.resource_check_error || '';
  turn.runnable = gapRunnable(turn.compiled);
}

/** Narrow the sidebar to the resources an answer named.
 *
 *  Streams by default (ASSESSMENT-CHAT.md §3(b) — `POST /query/stream`
 *  existed and was unused; a static "Asking…" chip is worse than what was
 *  already built). Falls back to the blocking `ask()` only when the stream
 *  itself fails before any text arrived, so a proxy or browser that cannot
 *  do SSE still gets an answer rather than nothing. */
export async function submitAsk({ question = '', skipResourceCheck = false } = {}) {
  const input = $('ask-input');
  const q = (question || input.value).trim();
  if (!q) return;
  if (!question) input.value = '';

  // `entityType` is pinned at the moment the turn is asked, same as `slug` —
  // `state.resourceType` can change before the vote handler below reads it
  // back (the person can switch resources while an answer sits in the
  // pane), and a feedback POST must attribute the disagreement to the
  // resource type the question was actually asked about.
  const turn = {
    question: q, slug: state.selectedSlug, entityType: apiEntityType(state.resourceType),
    pending: true, streaming: true, answer: '',
  };
  state.chat.push(turn);
  renderTurnList();
  // Open the pane on this turn immediately — the whole point is that the
  // answer is read in the pane, not assembled invisibly behind a rail chip.
  await promoteChatTurn(turn);
  const placeholder = $('promoted-body');
  if (placeholder) placeholder.innerHTML = `<div class="text-answer text-ink-muted">Answering…</div>`;

  const opts = {
    resourceSlug: state.selectedSlug,
    entityType: apiEntityType(state.resourceType),
    perspectives: state.activePerspectives,
    sessionId: sessionId(),
    skipResourceCheck,
    investigation: state.investigation || null,
  };
  let sawChunk = false;
  try {
    for await (const evt of askStream(q, opts)) {
      if (evt.t === 'chunk') {
        sawChunk = true;
        turn.answer += evt.v;
        updateStreamingText(turn);
      } else if (evt.t === 'done') {
        applyAnswer(turn, {
          response: turn.answer, intent: evt.intent, query_hash: evt.hash, cached: evt.cached,
          chart: evt.chart, compiled: evt.compiled, symbol_table: evt.symbol_table,
          compare_symbols: evt.compare_symbols, alias_suggestion: evt.alias_suggestion,
          resource_question: evt.resource_question, held: evt.held, journal_proposal: evt.journal_proposal, exchange_id: evt.exchange_id, journal_written: evt.journal_written, resource_check_error: evt.resource_check_error,
        });
      }
    }
  } catch (err) {
    if (sawChunk) {
      // Partial text is still worth keeping — say the stream cut off rather
      // than discarding what already rendered.
      turn.pending = false;
      turn.streaming = false;
      turn.error = `The stream ended early: ${err.message}`;
    } else {
      // Nothing arrived yet — fall back to the blocking endpoint rather
      // than reporting a failure the plain path would not have had.
      try {
        const body = await ask(q, opts);
        applyAnswer(turn, body);
      } catch (err2) {
        turn.pending = false;
        turn.streaming = false;
        turn.error = `The question could not be asked: ${err2.message}`;
      }
    }
  }
  await promoteChatTurn(turn);
}

/** Update the pane in place while a stream is still arriving, without the
 *  full `promoteToPane()` round trip (which would reset scroll position on
 *  every chunk). Only meaningful for the 'inline' form — a chart or diagram
 *  cannot be drawn from a partial answer, so those wait for `done` and the
 *  full `promoteChatTurn()` call above handles them. */
function updateStreamingText(turn) {
  if (state.promoted !== turn) return;
  const body = $('promoted-body');
  if (!body) return;
  if (answerForm(turn) !== 'inline') return;   // chart/diagram/list forms redraw whole on `done`
  body.innerHTML = `<div class="whitespace-pre-wrap text-answer text-ink">${tnum(esc(turn.answer || ''))}</div>`;
}
