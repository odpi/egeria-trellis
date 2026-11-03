# Matrix round — what shipped, what it taught us, what we'd like your eye on

**From:** the implementation session, 2026-09-10
**Branch:** `re-experimental-ui` (PR #21, still draft)
**Status:** live and in real use on a twelve-repo work list

---

## The verdict first

From the project owner, after using it to triage real repositories:

> "The matrix is a big improvement — we can continue to make it better but it
> clearly helps us to triage and make decisions on resources. We will want to
> continue to refine but it is a good addition."

So: keep it, keep refining it. Everything below is in that spirit.

## What the matrix is now

Rows are resources, columns are the questions of the current stage, cells are
one glyph each. Columns are numbered and the full question text sits in a key
beneath the grid — the earlier version clamped question text into 120px
headers and at 27 columns nobody could read any of them.

Cell states, all glyph-plus-weight, no hue-only encoding:

| | |
|---|---|
| `✓` | answered |
| `∅` | ran, found nothing |
| `◐` | partial |
| `○` | not run · no surveyor |
| `⚠` | needs a person |
| `◔` | running |
| `▪` | **has results · not read** — new this round, see below |
| `?` | could not read |
| `·` | unclassified |

A `·` appended to any glyph marks a measurement older than seven days.

---

## Four things building it taught us

### 1. A fast path must not be allowed to lie

The matrix took ~65 seconds to appear because it read *full* facts for every
(resource, analysis) pair — running each analysis's results reader, whose
worst case reassembles 40,813 finding rows to derive a value no cell displays.

We replaced that with a projection: two grouped queries answering only what a
cell actually asks — *is there stored output, and when was it measured*.
**65s → 0.83s.**

But the projection genuinely cannot distinguish `measured` from `partial`;
that lives in a results dict only the reader produces. So it does not claim to.
That is where `▪ has results · not read` came from — **a new cell state
created by a performance constraint, not by a new concept in the domain.**

The alternative was to render those cells as `✓`, which would have been
faster *and* wrong. We think the general rule is worth stating: when a cheap
path knows less than the expensive one, the interface gains a state rather
than borrowing the nearest confident one.

### 2. There is no such thing as a resource's "as-of" date

We built a **Measured** column beside Resource, showing when that row was
last measured. The project owner rejected it immediately, and was right:

> "Having a single as-of date per repo doesn't make sense since each survey
> may have been run at a different time."

Confirmed in the data — on one row, `repository_health` was measured 8 hours
ago and the language classification 20 days ago. Any single date is wrong:
the oldest libels the fresh cells, the newest flatters the stale ones.

The column came out. **Dates live on cells.** If a row-level summary ever
comes back it can only honestly be a *span* ("just now – 20d ago"), never a
date.

### 3. A glyph cannot say what the answer was — so let people open it

The project owner's proposal, and it resolved several tensions at once:

> "What might make more sense is to be able to click on a given check mark
> (or other symbol) and being given a pop up with the latest survey results
> for that question?"

Clicking a cell now opens that question's latest results for that resource:
each contributing analysis, its state, its answer, **its own measurement
date**, and a re-run. It is also what makes the expensive read affordable —
you pay for the one cell someone is actually looking at, not for 27 × 12. And
what the popup reads is folded back into the grid, so an opened cell stops
being `▪` and becomes its real state.

The design consequence: **the grid carries state, the popup carries meaning.**
We stopped trying to make the cell say more than a glyph can.

### 4. An action must name what it would do

Two versions of the same mistake, both caught in use:

- "Re-run for this resource" quietly queued *every* analysis behind the
  question. Most questions have two or three, and they are not equally stale.
  Now each analysis has its own `re-run this one`, and the bulk action appears
  only when there is more than one, says how many, and names them.

- "Bring up to date" could have re-run everything. Instead it shows a plan
  before it queues anything: which analyses are stale and on how many
  resources, how many measurements are already current and will be skipped,
  and — as an unticked option — how many have *never* run, since a first run
  is not a refresh and can cost far more.

```
Bring up to date            12 resource(s) · all rows

Stale — measured more than 7 days ago:
  · foss_scorecard    — 8 resource(s)
  · chaoss_metrics    — 7 resource(s)
  · community_support — 7 resource(s)

26 measurement(s) are already current and will be skipped.
[ ] also run the 7 that have never run — a first run, not a
    refresh, and it can cost far more.

This queues 22 run(s), one batch per analysis carrying only the
resources that need it. Nothing runs until you confirm.
```

The seven-day threshold is **display-only**. It marks cells for attention; it
decides nothing, and nothing re-runs unless asked.

---

## An unexpected result: the interface found data rot

Two real defects surfaced only because the matrix tried to show measurement
dates. Neither was visible from any other surface.

1. **A word in a timestamp column.** `surveyed_at` is the *read key* — the
   results reader returns only the rows at `MAX(surveyed_at)`. 3,269 rows had
   been written with the literal string `'probe'` from an ad-hoc session, and
   because any word sorts above every ISO timestamp this century, those rows
   had made one analysis **unreadable on five resources**: its real
   2026-09-01 run was invisible behind them. Now rejected at the write, and
   the rows have been cleared — the real runs are readable again.

2. **Two clocks disagreeing.** The run registry said one analysis last ran on
   24 August; its result rows had been written that same morning at 02:57.
   Something writes results without recording a run. We made the result rows
   win (they are the harder evidence) and pointed the cell and the popup at
   the same source, so they can never show one date each for the same
   measurement. The underlying cause is still open.

Worth noting as a design argument: **surfacing provenance is not decoration.**
Both of these had been sitting in the data for weeks.

---

## Where we'd like your eye

Ranked by how much we think it matters, not by effort.

1. **Scale in both directions.** Twelve rows × five columns is comfortable;
   twelve × twenty-seven scans but only because the columns are numbered. What
   happens at fifty repositories? Is there a design for the matrix that
   degrades gracefully, or does it want a different view past some size?

2. **Small screens — still unresolved.** Flagged in the previous round and
   still true. The frozen first two columns plus 26px cells work on a wide
   display; we have no design for a narrow one.

3. **The `▪` state's weight.** Is a distinct glyph right for "has results,
   not read", or should it read as quieter than a confident state rather than
   as a different one? It currently sits at `text-ink-muted`.

4. **The staleness mark.** A `·` appended inside a 26px cell is subtle to the
   point of nearly invisible. We deliberately did not recolour the glyph,
   because "this answer is old" and "this answer is bad" are different claims
   and the palette already spends its warn colour on the second. Is there a
   third channel available?

5. **The numbered-column key.** Readable and printable, but it costs a glance
   down and back for every column. If there is a better answer than numbers +
   a key, we have not found it.

6. **Stage-spanning matrices.** Today one stage at a time, so the stage is
   named once in the header. If rows from several stages were ever shown
   together, the per-row stage label that `/next` dropped becomes the first
   thing missing.

---

## Still open from earlier rounds

- The exit criterion for `/next` remains untested by anyone but us; PR #21
  stays draft until it is.
- Disposition **history** exists in the current UI and nowhere in `/next` —
  the only place the *sequence* of verdicts is visible.
- `/next`'s deferred "Search" panel still inherits the label and therefore
  mis-describes what the current UI's Search actually is (repo discovery, not
  resource search).
