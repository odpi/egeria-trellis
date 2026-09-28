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
import { ask, askStream, sendFeedback, submitAnswerFeedback } from '/static/re-api.js';
import {
  state, esc, icon, tnum, $,
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
      <span class="text-caps text-chrome-muted">⌘/Ctrl + Enter</span>
    </div>`;

  $('ask-submit').addEventListener('click', submitAsk);
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
function sourceLine(body) {
  const manifest = body.compiled && body.compiled.manifest;
  if (manifest && typeof manifest === 'object') {
    const parts = Object.keys(manifest).filter((k) => {
      const v = manifest[k];
      return Array.isArray(v) ? v.length : v != null && v !== '';
    });
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
    return (parts.length
      ? `From compiled evidence · ${parts.join(', ')}`
      : 'Compiled evidence was empty · answered from retrieval') + suffix;
  }
  return 'No compiled evidence on this answer · source not reported';
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
  'code_symbol_extraction', 'architecture_recovery', 'sub_resource_survey', 'manifest_parse']);
function listSources(body) {
  const packed = body?.compiled?.manifest?.packed;
  if (!Array.isArray(packed)) return [];
  return packed.filter((p) => p && p.role === 'evidence' && MEMBER_LISTED.has(p.key)).map((p) => p.key);
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
  dependency_analysis: 'dependencies', cve_scan: 'advisories', data_file_profiling: 'data files',
  api_structure: 'symbols', code_symbol_extraction: 'symbols', architecture_recovery: 'components',
  sub_resource_survey: 'sub-resources', manifest_parse: 'manifest entries',
};
function listSentenceHtml(l, i) {
  const mapped = LIST_NOUNS[l.key];
  // "68 dependencies · in 3 ecosystems" when the noun is known and the list
  // was grouped; "3 findings · security scan" when it is not.
  const group = l.field.replace(/^by_/, '').replace(/_/g, ' ');
  const head = mapped
    ? `<span class="tnum">${l.total}</span> ${esc(mapped)}${l.parts ? ` · in <span class="tnum">${l.parts}</span> ${esc(group)}${l.parts === 1 ? '' : 's'}` : ''}`
    : `<span class="tnum">${l.total}</span> ${esc(l.field.replace(/_/g, ' '))} · ${esc(l.key.replace(/_/g, ' '))}`;
  const partial = l.shown < l.total;
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
  return `<div class="mt-s2 text-chip text-chrome-ink">
    ${head}${
      partial ? ` · <span class="text-chrome-muted"><span class="tnum">${l.shown}</span> shown to the model</span>` : ' · all shown to the model'}${
      // The control says what pressing it does; it is the only clickable
      // part of the line, and the middot before it does the sentence
      // break's work.
      ` · <button data-list-source="${esc(l.key)}" data-list-slug="${esc(i)}"
          class="cursor-pointer bg-transparent p-0 text-accent-on-dark underline">open the full list${icon('chevron-right', { size: 13 })}</button>`}
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
function evidenceFooterListsHtml(lists, slug) {
  return lists.filter((l) => l.members).map((l) => listSentenceHtml(l, slug)).join('');
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
function renderTurnList() {
  const log = $('chat-log');
  if (!log) return;
  log.innerHTML = state.chat.map((t, i) => {
    const offScope = t.slug && t.slug !== state.selectedSlug;
    const isOpen = state.promoted === t;
    return `
    <button data-open-turn="${i}"
      class="block w-full cursor-pointer border-l-2 ${isOpen ? 'border-accent bg-chrome-line/20' : offScope ? 'border-chrome-line' : 'border-accent'}
             bg-transparent pl-s2 py-[2px] text-left">
      <div class="text-caps uppercase tracking-caps text-chrome-muted">
        You${t.slug ? ` · ${esc(t.slug)}` : ''}${offScope ? ' · not the current resource' : ''}
      </div>
      <div class="mb-s1 text-subtab text-chrome-ink">${esc(t.question)}</div>
      ${t.pending ? `<div class="text-chip text-chrome-muted">${t.streaming ? 'Answering…' : 'Asking…'}</div>` : ''}
      ${t.error ? `<div class="text-chip text-accent-on-dark">${esc(t.error)}</div>` : ''}
      ${t.answer && !t.pending ? `<div class="text-caps text-chrome-muted">${esc(t.source || '')}${isOpen ? ' · open in pane' : ' · tap to open'}</div>` : ''}
    </button>`;
  }).join('');

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

/**
 * Render the compile behind an answer — ASSESSMENT-CHAT.md §3(a), "open the
 * compile". `turn.compiled` is the SAME {text, manifest, derivation} shape
 * `POST /api/context/compile` returns (query.py's `_compiled_payload`),
 * kept on the turn in full since 2026-09-17 rather than reduced to just
 * `compileId` — the id told you a compile happened; this is what it
 * actually put in front of the model.
 *
 * Opens in the rail's evidence slot, same as `openMembers`/`showEvidence` —
 * one slot, one ticket, per app.js's own rule for that slot.
 */
function openCompile(turn) {
  ensureRailShowing();
  railClaim();
  const forWhat = turn.slug || '—';
  const compiled = turn.compiled;
  if (!compiled) {
    railFrame('Compile', forWhat,
      `<div class="text-chip text-chrome-muted">This answer carries no compile — it was answered from retrieval, or from a session with no resource in scope.</div>`,
      { sub: 'no compile' });
    return;
  }
  const manifest = compiled.manifest && typeof compiled.manifest === 'object' ? compiled.manifest : {};
  const manifestRows = Object.entries(manifest).filter(([, v]) =>
    Array.isArray(v) ? v.length : v != null && v !== '');
  const manifestHtml = manifestRows.length
    ? manifestRows.map(([k, v]) => `<div class="mb-[3px]"><span class="font-mono text-accent-on-dark">${esc(k)}</span>
        <span class="text-chrome-muted"> · </span>${esc(Array.isArray(v) ? `${v.length} item(s)` : String(v))}</div>`).join('')
    : `<div class="text-chip text-chrome-muted">The manifest carried no populated keys.</div>`;
  const text = String(compiled.text || '');
  const derivation = compiled.derivation;
  railFrame('Compile', forWhat, `
    <div class="mb-s2 text-caps text-chrome-muted">What the model was shown to answer “${esc(turn.question)}”.</div>
    <div class="mb-s3">${manifestHtml}</div>
    ${text ? `<details class="mb-s3">
      <summary class="cursor-pointer text-caps text-accent-on-dark">the compiled text · <span class="tnum">${text.length}</span> characters</summary>
      <pre class="mt-s2 max-h-[40vh] overflow-auto whitespace-pre-wrap text-chip text-chrome-ink">${esc(text)}</pre>
    </details>` : ''}
    ${derivation ? `<details>
      <summary class="cursor-pointer text-caps text-accent-on-dark">derivation</summary>
      <pre class="mt-s2 max-h-[30vh] overflow-auto whitespace-pre-wrap text-chip text-chrome-ink">${esc(JSON.stringify(derivation, null, 2))}</pre>
    </details>` : ''}`,
    { sub: 'the strongest provenance object in this codebase' });
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
        ${t.items.map((r) => `<tr class="border-b border-rule-soft">
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
  if (turn.aliasSuggestion) {
    const a = turn.aliasSuggestion;
    out.push(`<div class="mb-s3 text-caveat text-ink-muted">No resource named “${esc(a.term)}” was resolved —
      did you mean <span class="font-mono text-ink">${esc(a.candidate_name)}</span> (<span class="font-mono">${esc(a.candidate_slug)}</span>)?</div>`);
  }
  return out.join('');
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
    ${evidenceFooterListsHtml(turn.lists || [], turn.slug || '')}
    ${listButtons ? `<div class="mt-s2 flex flex-wrap gap-s2">${listButtons}</div>` : ''}
    <div class="mt-s3 flex flex-wrap items-baseline gap-s3 text-caveat">
      <button data-act="open-compile"
        class="cursor-pointer bg-transparent text-caveat text-accent-ink underline"
        >the evidence this answer was composed from ›</button>
      <button data-act="copy-turn"
        class="cursor-pointer bg-transparent text-caveat text-ink-muted underline"
        >${icon('copy', { size: 13 })} copy as evidence</button>
      ${turn.intent ? `<span class="text-ink-muted">intent ${esc(turn.intent)}${turn.cached ? ' · cached' : ''}</span>` : ''}
    </div>
    ${turn.queryHash ? `<div class="mt-s2">${feedbackHtml(turn, i)}</div>` : ''}`;

  footer.querySelectorAll('[data-list-source]').forEach((b) => b.addEventListener('click', () => {
    const slug = b.dataset.listSlug || state.selectedSlug;
    if (!slug) return;
    openMembers({ slug, analysisId: b.dataset.listSource, title: b.dataset.listSource.replace(/_/g, ' ') });
  }));
  footer.querySelector('[data-act="open-compile"]')?.addEventListener('click', () => openCompile(turn));
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
}

/** Narrow the sidebar to the resources an answer named.
 *
 *  Streams by default (ASSESSMENT-CHAT.md §3(b) — `POST /query/stream`
 *  existed and was unused; a static "Asking…" chip is worse than what was
 *  already built). Falls back to the blocking `ask()` only when the stream
 *  itself fails before any text arrived, so a proxy or browser that cannot
 *  do SSE still gets an answer rather than nothing. */
export async function submitAsk() {
  const input = $('ask-input');
  const q = input.value.trim();
  if (!q) return;
  input.value = '';

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
