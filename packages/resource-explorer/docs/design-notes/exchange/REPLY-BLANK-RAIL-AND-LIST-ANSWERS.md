# The blank rail, and what a list answer should be

**Date:** 2026-09-12 · **Read against:** `origin/main` at `19f3733` (after
`#52`), not the tree I reviewed this morning
**Prompted by:** two screenshots from the owner — an `evidence` click that
produced an empty rail on Discovery, and *list the dependencies* answered as
ten bullets with *Open as candidates (11)*.

`REVIEW-FIXES-IMPLEMENTED.md` and `CURATE-IMPLEMENTED.md` both landed first,
and they change two of my answers. The guard is now AST and per-function with
its failures recorded; the naming split went the way I framed it; the
timezone thing I could only flag as *cannot tell* was real and is fixed with
one `whenMs()`. Curate is built. Taking all of that as read.

---

## 1 · The blank rail

**One cause of it you already found.** *The sub-resource count opened an empty
rail: the members fallback read findings under the analysis id, and they are
stored under a different kind.* That is the same symptom the owner
photographed, so the first question is whether these shots predate `#51`. Worth
settling before anyone chases the rest.

**What is still live on `19f3733`, either way:**

`showEvidence` opens the drawer by asking the *preference*, not the *DOM*:

```js
if (!railIsOpen()) setRailOpen(true);        // app.js:5822
```

and `railIsOpen()` reads localStorage (`:2355`), while `renderIntentNav` runs

```js
setRailOpen(railIsOpen() && !shellIsNarrow(), { persist: false });   // app.js:580
```

on every stage switch. That `{ persist: false }` is deliberate and right — a
laptop preference is not a phone instruction — but it means the class and the
preference legitimately disagree. On a narrow shell, and after any narrow
toggle, `railIsOpen()` answers *true* while `#app-grid.rail-closed` is hiding
the pane. The guard then declines to open a rail that is shut, the panel is
written into `display:none`, and the click does nothing, silently.

The guard should read the thing it is guarding: `!$('app-grid').classList
.contains('rail-closed')`. One line, and it is the difference between a
preference and a fact.

**And the early return writes nothing at all:**

```js
if (!out || !env || env === 'loading' || env.__error) return;   // app.js:5824
```

An HTTP error is stored upstream as `{__error}` and lands here as silence. A
200 with no facts *does* get *No facts on this envelope.* — so the code already
knows the rule and applies it to the honest case while the failure case falls
through the trapdoor. That is backwards: **a failure is more owed a sentence
than an emptiness is.**

**What the screenshot rules in.** The Discovery rail is not narrow and not
hidden — it occupies its full column and is empty *including the composer and
the Ask button*. So this instance is not the guard: the rail's own frame is
gone, not just its evidence slot. Either `renderRail()` produced nothing, or
something threw mid-template (the string is built before assignment, so a throw
leaves the slot untouched and silent, which is indistinguishable from the rest
without a console). One console line from the owner settles it — if these shots
are post-`#51`, that is the thing to look at.

### The rule that makes the class of bug impossible

Three writers share `#rail-evidence` — `showEvidence` (`:5822`), `openMembers`
(`:3918`, opening only after two awaits) and the enrichment one (`:5094`, which
you fixed and gave a heading that names what it replaced). That heading is the
fix generalised, so make it the rule for all three:

- **The rail always renders its frame.** Heading, source line, and body. A
  writer replaces the body; nothing ever replaces the frame with nothing.
- **The heading names what is showing and for what** — *Evidence · license ·
  for `egeria-python`* — so a panel that lost a race is visibly the wrong
  panel rather than a plausible one.
- **Every terminal state is a sentence.** Loading, failed, empty, and
  *nothing was requested* are four different things, and the fourth is the one
  that currently renders as blank.
- **A slot with three writers needs a request id.** `openMembers` has no
  staleness check at all; a member fetch in flight overwrites a
  later-clicked evidence panel. Last writer wins should mean *last click*
  wins, not *last response*.

This is *absence and failure are states, never blanks*, applied to the one
surface where it was never applied.

---

## 2 · List answers — both, side by side

The owner's call, and it is the right one. Prose and the list are different
organs and neither should do the other's job.

**What went wrong is not the truncation.** `MAX_FULL_DICT_ITEMS = 10`
(`context_compile.py:373`) is a sound budget rule and it marks its elision
honestly — *… and 22 more*. The model then turned that marker into *Here are
some of them*, and the answer carried two numbers that disagree: **32** in the
sentence, **10** on the page, and nothing saying which is the list. The
compiler was honest and the prose channel laundered it.

So:

**In the rail — one sentence, the whole count, and a way out.**

> **32 declared dependencies**, in 3 ecosystems. Ten are shown to the model;
> the full list is in the pane. → *Open the list*

The sentence carries the total because the total is known — `members_for(...)`
returns `total` = 32 without a cap. Prose never enumerates. *Here are some of
them* stops being possible because the prose never holds the items.

**In the pane — the member tree, which already exists.**
`GET /api/projects/{slug}/members/dependency_analysis?scope=all&limit=1000`
returns all 32 grouped by ecosystem with `truncated` per group. This is
*counts open what they counted*, and the chat answer becomes one of the things
that opens it. Where a group is capped, the group says so — the flag is
already in the payload and should not be dropped on the way to the eye.

**The two must agree out loud.** If the pane shows 32 and the rail says 32,
the sentence *ten are shown to the model* is what keeps the model's
partial view from reading as the corpus. That clause is not an apology; it is
the provenance of the sentence beside it.

### Kill the candidate heuristic

```js
const lines = String(text || '').split('\n') …
  .filter((l) => l && l.length < 80);
return lines.length >= 3 ? lines.slice(0, 25) : [];   // app.js:2085-2090
```

`N` is the number of answer lines under 80 characters. That is why it read
**11**: the lead sentence plus ten bullets. It has never counted
dependencies, and `showCandidates` then intersects those lines against
registered project slugs — which, for dependency names, matches nothing. A
button that promises eleven things and can deliver zero is worse than no
button, and the comment above it already states the principle it breaks: *an
action that appears on every answer teaches people to ignore it.*

Candidates should come from the **member set**, not from the prose: offer it
only when the answer is backed by a members payload whose rows are resolvable
resources, and label it with that payload's count. Everywhere else, no button.

---

## 3 · The saveable report — it is a Record, and Curate just built one

This is where my answer changed today. Yesterday I would have specified a new
type. `#51` makes that wrong.

Curate's commit is *an append-only row: who, when, what was selected, what the
manifest said*, with steps writing their outcomes to it as they land. Strip the
Egeria steps and that is exactly a report: **a named, dated, authored record of
what was true about a selection at a moment, with its provenance attached.**
The shape is built, tested, and already carries the two properties a report
lives or dies on — the author is the server's, and the selection is a snapshot
rather than a query.

So: **one Record, two kinds.** A *catalogue* record has steps and outcomes. A
*report* record has none — it is the act of writing the list down. Both list
under the resource, both are append-only, both say *a snapshot, not a query*,
and both are corrected by a new record rather than an edit, which is the rule
Curate already states about reversal.

What a report record holds: the question as asked, the resource, the
analysis ids and their run dates, the full rows (name and detail) with the
total, any facet, the author, the written-at. Markdown and CSV become
**exports of** it rather than the thing itself — which is the gap the current
UI has: `copy as evidence`, the transcript, and the turn copier are all
clipboard-only, so every provenance line the project works so hard to compose
evaporates the moment someone pastes it somewhere. The only file the app
writes today is `inventory.csv`.

Three details that are design, not plumbing:

- **A report of a truncated list must say so in its header**, not in a
  footnote: *32 of 32 dependencies* or *200 of 1,412 — capped by the request*.
  A saved artifact outlives the person who knows about the cap.
- **The name proposes itself and the proposal is not clever.** Learn from
  promotion: `egeria-python — 32 dependencies, 2026-09-12`, and if the person
  types over it, the typed name wins until they clear it. Do not infer intent
  from the string.
- **Promotion's three acts apply to a report too** — a report is a thing you
  can hand to someone, and *note in journal* is the cheap one. The fourth act
  is still not *ignore*.

---

## Order

The rail guard and the four states are small and they are what the owner hit.
The candidate heuristic is a deletion plus a condition. The side-by-side list
answer is the real round — the pane half already exists, so it is the rail
sentence, the link, and the agreement between the two numbers. The report
record is the round after, and it is mostly naming: the machinery arrived with
Curate.

Component column and the wire diagram are still mine and still open. I have not
forgotten them.
