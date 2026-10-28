# Item 9 · Chat — assessment

**Asked for:** the owner's *"a lot of under-utilised power that needs to be
flushed out"*
**Read against:** `main` at `7b7bc2b4` (after `#123`)
**Status:** assessment only. The scoping decision at the end is one I am
proposing, not ruling.

---

## 1 · Nothing is missing. It is built, and then squeezed into 290px

I expected to find unwired capability. Almost everything is wired:

| capability | where | state |
|---|---|---|
| conversational continuity | `sessionId()` (`app.js:1983`), persisted as `re-next.sessionId`, sent on every ask | **works** — `ConversationAgent` gets the same session (`query.py:24`) |
| provenance per answer | `sourceLine()` (`app.js:2060`) reads `body.compiled.manifest` | **works, and is exemplary** — it distinguishes compiled evidence from retrieval and says *"source not reported"* rather than guessing |
| the compile behind an answer | `turn.compileId` captured at `app.js:2077`; server side `_compiled_payload` (`query.py:113`) | **captured, never opened** |
| per-answer vote key | `turn.queryHash`, with a comment that it always comes off the server so a vote lands under one key | **captured** |
| charts | `_pick_chart` (`query.py:140`) returns a Plotly figure | built, and the code says *"far too wide for the rail"* |
| diagrams | mermaid, rendered server-side via Kroki (`/api/diagrams/mermaid`) | built, and the code says *"A diagram cannot live in a 290px rail"* |
| structured answers | `_code_inventory_table`, `_compare_symbols_table`, `_fuzzy_alias_suggestion` (`query.py:335, 404, 488`) | built |
| streaming | `POST /query/stream` (`query.py:233`) | **built and unused** — the rail shows a static *"Asking…"* chip |
| the transcript as evidence | `copyAsEvidence(turnAsMarkdown…)` (`app.js:2055`) | **works** |

**Two comments in the source already name the problem**, which is the strongest
evidence that this is a placement question rather than a capability one.

## 2 · The decision has already been made, one surface over

`SPEC-THE-STAGE-PAGE.md` ruled it for analyses: **one object, one entry, and one
place for the fact — the pane under the answer.** The rail keeps provenance and
only provenance. The reason given then was that members had been pushed into the
rail because the pane *"held an empty ask box and eighteen hundred pixels of
nothing"*.

**Chat is the last surface still doing the thing that ruling fixed** — and it is
holding the ask box that sentence was about. So the proposal is not a new design:

- **The answer opens in the pane.** Prose, chart, diagram, table — all four forms
  get the width they were built for. The Plotly figure and the Kroki SVG stop
  being squeezed.
- **The rail keeps the turn list and the provenance**, which is what it is good
  at: the `sourceLine`, the compile manifest, and off-scope marking (`/next`
  already marks a turn *"not the current resource"* — a nice touch worth
  keeping).
- **Nothing about the ask pipeline changes.** This is placement plus two links.

## 3 · The three things worth building beyond placement

Each is small and each connects to something already shipped.

**(a) Open the compile.** `turn.compileId` is captured and dropped. The compile
is literally *what the model was shown* — the strongest provenance object in this
codebase — and `_compiled_payload` already reads it server-side. A link, *the
evidence this answer was composed from ›*, and the rail shows the manifest. This
is the house rule (*counts open what they counted*) applied to the one place with
the best thing to open.

**(b) Use the stream.** `POST /query/stream` exists. A static *"Asking…"* on a
long answer is worse than what is already built, and this is the cheapest
perceived-speed win available anywhere in `/next`.

**(c) Join `queryHash` to the gaps loop.** Item 8 just built per-answer feedback
landing as destination `ours` in the gaps collection
(`ITEM-8-FEEDBACK-IMPLEMENTED.md`). Chat captures `queryHash` for exactly that
purpose. **Check whether they are already joined** — if the chat answer's vote
does not reach `gaps.py`, that is one call, and it closes the loop the
destinations work was written for.

## 4 · And one candidate I am flagging, not proposing

`copyAsEvidence(state.chat.map(turnAsMarkdown))` already treats a transcript as
evidence — but only onto the clipboard. This app has a Records concept for
exactly that (`SPEC-REPORT-ACTS.md`, shipped in `#83`): an append-only row with a
server-stamped author and a snapshot.

**A conversation that produced a finding is a candidate Record.** It has an
author, a time, a provenance chain and a result. I am not proposing it in this
round — it needs a decision about whether a conversation is a thing this project
wants to keep, and that is the owner's call, not a layout question.

## 5 · What I am not deciding

- **Whether chat gets its own stage** rather than living beside the resource
  panes. The pane-versus-rail move above works either way, and the stage
  question needs to be answered by how people actually use it — which is item
  8's feedback data, once there is some.
- **Whether the classic chat panel gets any of this.** By
  `RULING-CLASSIC-AND-NEXT.md`, classic must stay *honest* but need not gain
  capability, and nothing here makes classic say something false. So: no
  obligation, and it is a free choice.

## 6 · Scope, if this is taken

Placement (§2) and the three in §3 are one round, in one module —
`next/stages/chat.js` once the `app.js` split lands, or beside it. No backend
change except possibly (c), which may already be done.

**Not in scope:** the Record question (§4), the stage question (§5), and anything
about classic.
