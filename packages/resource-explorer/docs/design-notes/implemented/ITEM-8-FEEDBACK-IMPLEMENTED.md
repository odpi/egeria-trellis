# Item 8 — Feedback: implemented

**Replies to:** `PLAN-FINISH-REPOS.md` Part 3, item 8 — *"Feedback — present,
not used. Owns `next/feedback.js`, `web/routes/feedback.py`, `gaps.py`. Done
when: per-answer feedback exists, and a disagreement lands in the gaps
collection as destination `ours`."*

**Branch:** `re/feedback-gaps`, worktree `.claude/worktrees/wt-feedback`.
Nothing was written in the main checkout.

---

## What was there before

Two feedback surfaces existed and neither was about an answer to a catalogued
question:

- the floating **Feedback** button (`next/feedback.js`, loaded from
  `index.html:425`) — about the *product*: a rating, a category, a message;
- **chat-turn votes** (`app.js:2333 feedbackHtml`) — thumbs on a chat answer,
  keyed by `query_hash`, going to the RAG feedback store.

The question rows — the main `/next` surface, the place a curator actually
reads an answer — had no feedback control at all, and no feedback of any kind
reached the gaps collection.

## What now exists

**1. A per-answer control on every answered question row.**
`next/feedback.js` appends *"Was this right? · Right · Partly · Wrong"* under
the provenance line. Clicking **Wrong** asks, once, what is wrong (optional
text); the others post immediately. Every outcome replaces the control with a
sentence naming what was recorded — including failures, so a dispute cannot be
silently dropped.

It attaches with a `MutationObserver` on `#question-rows` plus a delegated
`document` click listener, and **does not touch `app.js`**. Two reasons, both
deliberate: `app.js` is being split into per-stage modules by `wt-honest`, and
this control must survive that split untouched; and `app.js` rewrites a row's
`innerHTML` in place as its answer lands (`replaceRow`), so anything bound once
would vanish.

Rows are selected by the presence of a `[data-evidence]` button — `app.js`'s
own marker for `answered | automatic`. A row that never ran gets no control,
because there is no claim there to agree or disagree with.

**2. `POST /api/feedback/answer`** (`web/routes/feedback.py`).
Takes `{slug, question, verdict, comment?, analysis_id?}` where verdict is
`agree | partly | disagree`. It:

- resolves which analyses answer that question from the **question catalog** —
  the same mapping the page rendered from — rather than trusting the client;
- rejects an `analysis_id` the catalog does not list for that question, so a
  stale page cannot charge a dispute to an analysis that never answered it;
- records **every** verdict in the feedback store, so *"nobody has questioned
  this answer"* and *"someone confirmed it"* do not look alike;
- on `disagree` only, raises a gap and returns it, with `gap_reason` naming
  what happened either way (`gap: null` is never left to be read as a failure).

**3. A person's disagreement in the gaps collection, marked `ours`**
(`gaps.py: record_disagreement`). Same `upsert_gap` the measured gaps use, so:

- `destination` is `ours` — `destinations.py`'s rule 1, computed and never
  declared: the repository is not at fault for our answer about it;
- identity is `(slug, analysis_id, "disagreement", "question:<text>")`, so a
  second person disagreeing with the same answer **refreshes one row**. The
  count stays *"how many answers are disputed"*, not *"how many clicks"*;
- the `question:` prefix makes collision with a real `check_registry.yaml`
  check name impossible, and a later `record_gaps_for()` (which runs on every
  page load) leaves a person's gap standing — tested.

**4. `source` and `destination` on every gap row.** `gaps_summary` now states
`destination: ours` on every row (one word for both halves of the collection)
and `source: measured | person`, plus a `disputed_by_a_person` count. The two
kinds are both `disagreement` and both `ours`, but they are work for different
people — a disputed answer for whoever wrote the analysis, a measured
disagreement for whoever reconciles two measures — so they are distinguishable
without being separate buckets. `disputed_by_a_person` is a **subset** of the
`disagreement` count; the docstring says so, because adding them would
double-count.

## What I scoped out

- **The gaps *pane*.** This makes disputes land and be queryable
  (`GET /api/projects/{slug}/gaps` already returns them, now with `source` and
  `destination`). Rendering the collection as a stage surface belongs with
  whoever owns that pane; it is not item 8's done test.
- **Resolving a gap from the UI.** `upsert_gap` deliberately never resolves or
  reopens; `resolved_at` and the RFA route already exist. Nothing here clears
  a dispute, so a disputed answer stays disputed until something explicitly
  says otherwise.
- **The chat-turn votes.** Left alone — they key on `query_hash` and feed the
  RAG store; folding them into this path would change what a thumbs-down on a
  chat answer means.
- **Merging the product-feedback button into this.** Kept separate on purpose:
  product feedback goes to whoever builds the UI, a disputed answer to whoever
  wrote the analysis. One control would put a real disagreement about a
  measurement into a triage queue for button placement.
- **A question whose answer names no analysis** is recorded with
  `analysis_id: ""` rather than a guessed one. The dispute is real; the
  attribution is not available. `evidence.analysis_attributed` says which.

## What I could not test

- **In the running app.** `:8810` serves from the main checkout, which is on
  `main` — this branch is not deployed there, and per the coordination plan I
  did not touch that checkout. **The per-answer control has therefore not been
  seen on screen.** Its two fragile points are named above and are the first
  things to check once this is merged and the main checkout fast-forwards:
  (a) the `[data-evidence]` row selector, (b) re-attachment after
  `replaceRow` — idempotence is keyed on the injected bar, not on a flag on
  the row, precisely because the row element survives its own `innerHTML`
  being replaced.
- **`window.prompt` for the comment.** It is the smallest thing that works and
  does not touch `app.js`'s modal machinery. If the designer wants it inline,
  that is a UI round, not a rework of this path.

## Tests

- `tests/test_gaps.py::TestAPersonsDisagreement` — 6: lands as `ours`;
  distinguishable from a measured disagreement; two people are one gap;
  an unattributed disagreement is still recorded; `record_gaps_for` does not
  clobber it; measured gaps still say `ours`.
- `tests/test_answer_feedback_gap.py` — 7: the route's happy path, that
  **agreeing raises no gap**, that every verdict is kept anyway, and four
  refusals (unknown verdict, unknown project, an analysis that does not answer
  the question, an empty question).

Both run against `pg_registry`, matching this codebase's convention for
registry-shaped tests.
