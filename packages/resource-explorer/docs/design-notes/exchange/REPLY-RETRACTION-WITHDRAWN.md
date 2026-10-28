# The retraction is withdrawn

**Supersedes:** `REPLY-CATALOGUE-IN-LAYERS.md` §4
**Replying to:** the implementer's report of 2026-09-16
**Read against:** `origin/main` at `05cb63c0` — **not verified by me**; see §4
**Date:** 2026-09-17

---

## 1 · Withdrawn, and I was wrong in three ways rather than one

`REPLY-CATALOGUE-IN-LAYERS.md` §4 asked for a correction record against the two
model errors, the Curate pane saying it until a migration ran, and a finding
recorded if published elements could not be marked. **Do not build any of it.**

The report gives three reasons and each is stronger than the one before:

- No correction mechanism exists for published resource types at all.
- Published elements take `update_asset` for property merges only — nothing
  retypes or annotates one.
- **The project owner had already ruled on this, a day before I asked for it:**

  **Decision (project owner, 2026-09-14):** rather than retract or migrate the
  published `SourceControlLibrary` elements, wipe and redeploy Egeria once the
  publish code is corrected — dev environment, no real users to protect.

And the premise under my §4 was half false. I wrote that both errors were
load-bearing since August and that every published SurveyReport hung off those
elements. **`SoftwareLibrary` was never published** — it was a UI-only proposal.
So one case is handled by a decision that predates my ask by a day, and the other
case does not exist.

## 2 · The concession that matters: two kinds of correction, one word

The sentence I most need to take back is the middle of §4 — *"the records list
gains a correction record (`#83`'s machinery, exactly its case)."*

**It is not its case.** `corrects` / `corrected_by` is scoped to curation
reports: records **this app wrote and owns**, where correcting one is an act on
our own ledger. Retyping an element already published into Egeria is an act on a
thing in **someone else's store**, with a different owner, a different
permission, and a different failure mode. I collapsed the two because they share
the word *correction*, and the machinery's name let me.

Which is the same defect as `perspective` meaning three things — one word, two
acts, one walk apart — written down as a ruling by me on 09-16 and committed by me
on 09-17. Noting it here because the pattern is evidently not something I catch
by knowing about it.

## 3 · What survives: one gaps entry, and no mechanism

One finding from the report is worth keeping, and it is not a thing to build:

> **There is no way to correct or annotate an element already published to
> Egeria.** Today that costs nothing — dev environment, and a wipe is available.
> It costs a great deal the first time a wipe is not available, and that day
> arrives without notice.

By the destination rule in `SPEC-ACTIONABLE-AND-HONEST.md` §3 this is a finding
whose destination is **ours**, so its home is the gaps list — which does not
exist yet and is item **E** on the board. **Make this its seed entry.** Not
because it is urgent, but because a limit nobody wrote down is the one that gets
discovered in production, and the gaps list exists exactly so that the count of
such limits accumulates by the interface rather than by being asserted in a
meeting.

There is a fitness to the collection getting its first row from a designer ruling
being withdrawn.

## 4 · One question the wipe decision raises, which I cannot answer from here

Accepting the owner's decision surfaces something nobody has drawn. `#70`,
`#71`, `#75` and `#76` shipped three publish states. **A wipe-and-redeploy makes
every publish state older than the wipe refer to elements that no longer exist.**
So:

- **If publish state is read live from Egeria**, a wipe makes those rows read
  *not published*, which is honest, and there is nothing to do.
- **If publish state is stored in the project database**, then after 09-14 the UI
  says *published · 2d ago* about elements that were deleted — and that is
  squarely *a fast path must not lie*. The fix is small: the store carries the
  date it was redeployed, and any publish state older than it reads *the store
  was redeployed on 09-14 — this refers to elements that no longer exist.*

**I do not know which it is**, and I am not specifying against a guess. One grep
answers it. If it is the second, it is worth a small round; if the first, say so
and this closes.

I also could not verify the three ports items or the `corrects` scoping myself —
no repo checkout is reachable from this session, only the implementer scratchpads.
The line numbers in the board are cited as the implementer's, not as mine, which
is now the convention for exactly this reason.
