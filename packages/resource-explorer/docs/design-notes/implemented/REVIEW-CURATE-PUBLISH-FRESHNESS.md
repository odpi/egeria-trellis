# Review: Catalogue's publish step re-surveys only what's stale

**Reviewing:** `CURATE-PUBLISH-FRESHNESS-IMPLEMENTED.md`, `db2f3616`
**Read against:** `origin/main`
**Date:** 2026-09-21

---

## 1 · Accepted, and three things in it are better than the ask

The owner asked for two things — wire in the existing freshness gate, and never
run an analysis that has never run. Both are there, and the work around them is
the good part:

- **`REPO_ANALYSIS_SOURCE_STEPS`, not `REPO_ANALYSIS_STEP_MAP`.** The first pass
  used the ownership partition for the "what do I run to refresh this" question,
  and `TestOwnershipMapIsOnlyUsedForAttribution` caught it immediately. That test
  exists precisely to catch that substitution — someone anticipated this mistake
  and left a tripwire, and it fired. Worth noting because it is the cheapest
  kind of defence this project has and it just paid for itself.
- **`steps=[]` as the asset-creation path.** Verified by reading
  `SurveyOrchestrator.run` rather than assumed — `set([]) & STEP_REGISTRY.keys()`
  is empty, no surveyor runs, a normal `SurveyResult` comes back — and the
  reasoning for *not* building a separate "just create the asset" path is
  correct: `_find_or_create_asset` is idempotent, so the defensive call is
  cheaper than the abstraction.
- **The detail message now says which branch happened.** The old text — *"surveying
  first, then publishing"* — was true regardless of what occurred, which is the
  definition of a sentence that reports nothing. Replacing it with the actual
  outcome was not asked for, and it is the honesty rule applied without
  prompting.

`assess_freshness`'s own docstring deserves a mention too: *"A run recorded as
`error` does NOT count as freshness — its data is the old data, and refusing to
re-run after a failure is the one behaviour nobody would want."* That is the
right call stated in one sentence.

---

## 2 · One thing to add: the never-run set is now silent

The detail message reports *"N of M **previously-run** analyses already fresh and
skipped."* Correct as far as it goes — and analyses that exist in the catalog but
have **never run for this repository** are neither counted nor mentioned.

They are correctly **not run**; that is the ruling and I am not reopening it. But
they are currently rendered as *absence* rather than as *a state*, and this
project's whole discipline is that those differ. The consequence is concrete:

> **A newly added analysis will never reach an existing repository through
> Catalogue**, because it has no run history and therefore never enters the
> fresh/stale split at all.

That may be exactly what is wanted — a new analyser should probably not start
itself on a hundred repos. But the curator cannot see that it is sitting there.
One clause, in the shape the message already uses:

> …; **3 analyses have never run here and were not started ›**

The link opens which ones. Then "never run" is a state a person can act on, and
the default stays *do not start it for them*.

---

## 3 · One limit worth stating, not a request

**Freshness is the age of our answer, not whether the thing changed.**
`assess_freshness` takes `max_age_seconds` from `runs.freshness_seconds`, so a
repository that has not changed at all still goes stale on the clock and gets
re-surveyed on the next Catalogue.

The saving is real — *not twice within the window* — but it is bounded, and it is
worth saying plainly so nobody expects more from it. The owner's question was
*"why would we execute and run surveys that the user isn't interested in?"*, and a
time-based gate answers a narrower question than that one.

For repositories specifically there is a cheaper and more truthful signal
already collected: **the last commit we recorded.** Fresh-if-newer-than-the-last-
commit-we-know-about would mean an untouched repo is never re-surveyed at all,
which is what the question was really asking for. **I am not specifying it** —
it touches `assess_freshness`, which this change deliberately did not, and it
wants its own round. Recorded so the limit is visible rather than discovered
later.

---

## 4 · Nothing to change before this ships

§2 is one clause and can ride with the next Curate round; §3 is a note. The
change as built is correct, well-tested, and scoped exactly where it said it
would be.
