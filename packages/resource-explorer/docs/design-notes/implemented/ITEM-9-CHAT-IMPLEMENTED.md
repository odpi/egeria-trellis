# Item 9 — Chat: implemented

**Replies to:** `PLAN-FINISH-REPOS.md` item 9 — *"Chat — under-utilised. Owns
`next/chat.js` (extract). Done when: assessed first, then scoped — the only
item here with no agreed shape yet."* The design round happened first —
`ASSESSMENT-CHAT.md` — and this implements its §2 placement move plus the
three follow-ups from its §3, in priority order.

**Branch:** `re/chat-stage-page`, worktree `.claude/worktrees/wt-chat`.
Nothing was written in the main checkout.

---

## What the assessment found, and what this does about it

`ASSESSMENT-CHAT.md`'s finding was that almost nothing needed building:
session continuity, `sourceLine()`, the compile id, the vote hash, Plotly
charts and server-rendered Kroki diagrams all worked already — squeezed into
a 290px rail, with two comments in the source ("far too wide for the rail",
"a diagram cannot live in a 290px rail") naming the defect as placement, not
capability. `SPEC-THE-STAGE-PAGE.md` had already ruled this shape wrong for
analyses: *one object, one entry, one place for the fact — the pane, under
the answer; the rail keeps provenance only.* This item applies that same
rule to chat, the surface the assessment called "the last surface still
doing the thing that ruling fixed."

## 1 · Extraction: `next/chat.js`

`next/chat.js` did not exist — chat was ~370 lines inline in `app.js`
("The right rail — Ask, scoped here"). Moved out: `sessionId`, the rail
chrome (`renderRail`/`renderRailScope`), `sourceLine`, `extractMermaid`, the
list-sentence machinery (`listSources`/`listSentences`/`listSentenceHtml`),
the turn-list renderer, the vote bar and `vote()`, `submitAsk`, and
`turnAsMarkdown`.

**Kept in `app.js` and exported**, deliberately not moved: `promoteToPane`,
`answerForm`, `copyAsEvidence`, `railFrame`/`railClaim`/`ensureRailShowing`,
`openMembers`. These are shared with the Questions-checklist's own
row-promotion path (`showDiagram`/`showEvidence` promote a fact's diagram or
a chart into the same pane chat uses) — `stages/understanding.js`'s own
header comment already documents this exact split for `chartLayout`/
`drawChart`/`tokens`, and this item follows the same line rather than
forking a second copy of pane-promotion into chat.js.

`chat.js` imports `state`/`esc`/`$`/etc. straight from `app.js`, the same
choice `stages/understanding.js` made (not `rfa.js`'s — see that file's own
header comment for why it went the other way). Chat's rendering is
inseparable from the pane/rail chrome app.js owns; carrying a second copy of
that chrome would be exactly the two-copies-of-one-fact bug this project
keeps finding in its own docs.

## 2 · The placement move (§2)

**The rail is now the turn list and provenance only.** `renderTurnList()`
(was `renderChatLog()`) shows the question, who it was about, and a one-line
status — no answer body, no chart/diagram "open in pane" escape hatch, no
vote bar. Clicking a turn opens it.

**Every answer opens in the pane**, not just chart/diagram. `submitAsk()`
calls `promoteChatTurn()` twice: once immediately (so the question is
visible and streaming text has somewhere to land) and once after the answer
is complete (so the final chart/diagram/table renders at full width).
`promoteChatTurn()` wraps the shared `promoteToPane()` — which still does
the actual rendering of prose/chart/diagram — and appends what is
chat-specific below it: the list-sentences an answer was compiled from, a
"was this right" vote bar, "the evidence this answer was composed from ›",
and copy-as-evidence. The source line itself is printed once, by
`promoteToPane()`'s own header — the footer does not repeat it.

## 3 · The three follow-ups (§3)

**(a) Open the compile — done.** `turn.compiled` now keeps the FULL
`{text, manifest, derivation}` object `_compiled_payload()` (query.py)
already produced, not just `compileId`. "the evidence this answer was
composed from ›" opens it into the rail's evidence slot (`openCompile()`,
using the same `railFrame`/`railClaim`/`ensureRailShowing` one-slot
machinery `openMembers`/`showEvidence` already use) — the manifest's
populated keys, the compiled text behind a `<details>` (it can be large),
and the derivation if present. A turn with no compile (retrieval, or no
session) says so rather than opening an empty frame.

**(b) Use the stream — done.** `askStream()` (re-api.js) is a small async
generator over `POST /api/query/stream`'s SSE frames. `submitAsk()` consumes
it by default: each `chunk` event appends to `turn.answer` and, if that turn
is the one currently open in the pane, redraws just the text (no full
`promoteToPane()` round trip mid-stream, so scroll position survives). The
terminal `done` event is folded through the same `applyAnswer()` helper the
non-streaming path uses, so the two response shapes cannot drift into two
renderings of "an answer." Two fallbacks, both stated on the turn rather
than silent: a stream that fails **before** any text arrived falls back to
the blocking `ask()`; one that fails **after** some text arrived keeps the
partial answer and says the stream ended early. The `done` event's
`symbol_table`/`compare_symbols`/`alias_suggestion` payloads — real fields
`query.py` already produced but the non-streaming path never returns — now
render too (`structuredTableHtml()`), a small table each, in the pane rather
than the rail.

**(c) Join the vote to the gaps loop — done, additively.** Confirmed this
was a real gap, not a maybe: `vote()` only ever called `sendFeedback()` →
`POST /api/query/feedback` → `MetricsCollector.record_feedback` — a
different consumer than `gaps.py`'s `record_disagreement`, which item 8's
"Was this right?" checklist bar posts to via `POST /api/feedback/answer`.
Worth noting item 8's own doc (`ITEM-8-FEEDBACK-IMPLEMENTED.md`) explicitly
scoped this OUT at the time ("chat-turn votes — left alone... folding them
into this path would change what a thumbs-down on a chat answer means") —
`ASSESSMENT-CHAT.md` reopened exactly that question, and this closes it.

`vote()` now calls both endpoints: `sendFeedback` unchanged (so
MetricsCollector's tracing keeps working), and — **only when the turn has a
resource in scope** — `submitAnswerFeedback()` against the same
`/api/feedback/answer` route, mapping the three vote values to
`agree`/`partly`/`disagree`. A chat question is free text, not one of the
catalog's canonical questions, so the server's `_analyses_for_question`
lookup will almost always find nothing and record the disagreement
attributed to no analysis — that is the endpoint's own documented,
honest behaviour for an uncatalogued question, not a failure of this
wiring; the gap is still real and still counted. The server's own
`gap_reason` sentence is shown next to the vote rather than one composed
client-side, matching how the checklist bar already reports it.

**The one real boundary, named rather than routed around:** a turn asked
with no resource selected has no `slug`, and `/api/feedback/answer` 404s on
an unknown project — gaps are inherently per-resource. That turn's vote
still reaches `sendFeedback` (MetricsCollector doesn't care about scope);
it just cannot join the per-resource gaps collection, and the turn says so
("not joined to the gaps loop — no resource was in scope for this
question") rather than silently doing nothing.

## What was deliberately not built

- **`copyAsEvidence`-as-a-Record.** `ASSESSMENT-CHAT.md` §4 flags this
  explicitly as the owner's call, not an implementer's — a decision about
  whether a conversation is a thing this project wants to keep as a
  Record, not a layout question. Left untouched; `tests/test_next_chat_pane.py`
  pins that no `saveReport`/`actOnRecord`/`listRecords` wiring was added.
- **Whether chat gets its own stage** rather than living beside whichever
  stage is active (§5) — the assessment says this needs usage data from
  item 8's feedback, which does not exist yet. Out of scope here.
- **Whether classic gains any of this.** Per `RULING-CLASSIC-AND-NEXT.md`,
  classic must stay honest but need not gain capability — a free choice,
  not exercised in this round.
- **A richer symbol/comparison table UI.** `structuredTableHtml()` renders
  `symbol_table`/`compare_symbols`/`alias_suggestion` plainly (a `<table>`,
  a two-column grid, a sentence) rather than matching
  `SPEC-THE-STAGE-PAGE.md`'s `FactInPlace` disclosure exactly — these
  payloads only became reachable at all once (b) wired the stream in, and
  building them out fully is its own round once someone has seen one on
  screen.

## What I could not test

**Live, signed in.** `:8810`/the main checkout still serves `main`; this
branch is not deployed there and, per the coordination protocol, was not
touched. A throwaway dev server on port 8817 (this worktree's own checkout,
`.claude/worktrees/wt-chat`) confirmed:

- the server boots cleanly (`resource_explorer.web.app` startup, no errors);
- every `/next` static asset — `app.js`, `chat.js`, and all sibling
  modules — serves `200 OK`, including the new `chat.js`;
- `node --check` passes on `app.js`, `chat.js`, and `re-api.js` (loaded as
  ES modules);
- zero console errors on load.

The app requires an Egeria sign-in (2026-09-04 runtime plan — Egeria is the
identity provider) and this box has no `TRELLIS_ANONYMOUS_READ` override.
"Continue without signing in" is present but declined — confirmed via
`/api/auth/policy`/`/api/auth/defaults` that it does not admit anonymous
read here — and I did not type any password, demo or otherwise, per this
session's absolute rule on that. So the actual chat interaction —
streaming text landing in the pane, a chart or diagram promoting correctly,
the compile link opening, a vote landing in the gaps collection — has **not
been seen on screen**. Static/structural verification above is the honest
substitute; live verification is the first thing to do once this merges and
a signed-in session is available.

## Tests

`tests/test_next_chat_pane.py` — 23 assertions across seven groups, grepping
function bodies out of `chat.js`/`app.js`/`re-api.js` (this codebase's
established pattern for `/next` JS without a browser — see
`test_next_understanding_pane.py`, `test_next_admin_pane.py`):

- **Extraction** — `chat.js` exists and exports the moved functions; the old
  inline definitions are gone from `app.js`; the shared pane machinery
  stayed in `app.js`, exported, not duplicated.
- **Placement** — the turn list renders question/provenance and not the
  answer body/chart-promote-button/vote bar; clicking a turn promotes it;
  every arrival (not just chart/diagram) auto-opens in the pane; the source
  line is not printed twice.
- **Open the compile** — the full compiled object is kept on the turn; the
  link opens it into the rail's evidence slot via the shared
  `railFrame`/`railClaim`/`ensureRailShowing`.
- **Use the stream** — `re-api.js` exposes the SSE generator; `submitAsk`
  consumes it; a pre-first-chunk failure falls back to the blocking `ask()`;
  a post-chunk failure keeps the partial text; mid-stream updates skip
  chart/diagram/list forms.
- **Join the vote to the gaps loop** — the pre-existing `sendFeedback` call
  still fires; the vote-to-verdict mapping matches `feedback.py`'s
  `VALID_VERDICTS`; `submitAnswerFeedback` is called only when the turn has
  a resource in scope; the unscoped case states the boundary.
- **The two named source comments** are gone from the current rail
  rendering (they may still appear in a docstring recounting the history,
  which is fine).
- **The deferred Record idea was not built.**

Backend behaviour behind (a)/(b)/(c) — `_compiled_payload`, the stream's SSE
shape, `record_disagreement`'s handling of an unattributed `analysis_id` —
was already covered by `tests/test_answer_feedback_gap.py` and
`tests/test_gaps.py` before this item; no backend code changed, so no new
backend tests were needed.

Full suite: `uv run pytest tests/ -q -k "not Postgres"` — see the commit for
the pass count run against this branch.
