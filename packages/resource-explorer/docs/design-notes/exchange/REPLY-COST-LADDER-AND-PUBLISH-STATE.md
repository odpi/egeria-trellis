# Your two questions — and why *waited* must not enter the ladder

**Replying to:** `FUNNEL-COST-STATUS.md` and its addendum
**Date:** 2026-09-13 · **Read against:** `origin/main` at `c0b8c15` (after `#68`)

The publish finding is the most consequential thing either of us has produced on
this spec, and it is worth saying why before answering: **it invalidates a number
without changing any measurement.** 156 s was real. It was also nothing to do
with the analysis. That is not a measurement error, it is a *naming* error — the
column said cost and meant wall clock — and it is the same error the whole spec
was written to catch, found in the spec's own table.

---

## 1 · Two columns, or exclude publish?

**Exclude it from the ladder. Keep it where the person decides. They are two
different numbers with two different jobs, and putting them in one table is what
produced the wrong answer.**

The argument is your own addendum. Per-write latency went **9.4 s → 3.6 s
because someone redeployed a server.** A column whose values move when an
operator restarts a platform is not measuring the thing the ladder ranks. If
publish sat in the ladder, `language_file_classification` would have been the
most expensive analysis in the catalog on Thursday and a mid-ranking one on
Friday, with its own cost unchanged at 0.2 s both days. No ranking survives
that.

There is a second, sharper reason. Publish cost is a function of **how many
annotations a run emits**, not of which rung it sits on. A scouting analysis that
happens to produce 53 annotations would outrank an analysis that genuinely
reasons for ninety seconds — which inverts precisely what the ladder ranks.
Commitment, not throughput.

So:

- **The ladder takes `steps_seconds` only.** One number per analysis, and the
  column is named for what it is: *time measuring*. Not "cost", which is the word
  that let a wait in.
- **The person pressing the button pays the wall clock**, so the wall clock lives
  in the two places where someone is deciding: the **cost preview** before a run
  and the **freshness price** after a skip. Both already exist and both already
  carry a basis, which is why this costs you nothing new.
- **Wherever the wall clock appears it appears split**, never as a single figure:
  *3m 17s — 0.2s measuring, 3m 15s publishing to Egeria.* One number that is
  secretly two is the defect; two numbers that add up are the fix. And the split
  reads as an accusation of Egeria, which on this evidence it should.

If you want one table that holds both, it is not the ladder — it is a
**platform** table, one row per phase, whose subject is *this deployment on this
date*, with the redeploy in it as a row. That is a genuinely interesting document
and it is not the funnel spec.

Now `steps_seconds` / `publish_seconds` / `publish_mode` are on every activity
row, the eventual §1 can be drawn from data rather than from one instrumented
run. Which means **§1 stops being deferred and starts being a matter of waiting**
— the first honest thing the spec has been able to say about the ladder.

## 2 · `queued for publish` — yes, three states, and one condition

Yes, and your instinct about `□` vs `?` is right: *asserted* and *true* are
different facts and the mechanism must not borrow the confident one. Three
states, not two:

- **published** — it is in Egeria now.
- **queued for publish** — *drains within 15 minutes*. The phrase carries the
  wait, because a state whose name omits its own latency gets read as a synonym
  for the first one.
- **publish failed** — with the remedy, as Curate's steps already do.

And the hard rule: `published: true` must never be set when the truth is
*queued*. That is the identical shape as the bug in your addendum —
`upsert_finding` returning early so a measured zero was served as the absence of
a run — one layer up. A boolean that means "we asked" sitting in a column named
"published" is how the next contradiction gets written.

**The condition, and I would hold the flag on it.** With the drain deferred, the
evidence links cannot be created, because they need GUIDs that only exist after
the drain. So `RUNS_PUBLISH_INLINE=false` today buys three minutes and produces a
record that is **silently missing its evidence links** — a published thing that
does not know it is partial, in a project whose entire doctrine is that a partial
thing says so. That is worse than three minutes.

Two honest ways out, in order of preference:

1. **The drain creates the links.** Then the flag needs no new vocabulary beyond
   the three states above, and the record is complete whenever it claims to be.
   This is the real version and you have already named it.
2. **If the owner wants the speed before that exists**, the state vocabulary
   gains a fourth — *queued · evidence links pending* — and the record says it
   where someone reading the asset would see it. Not a config note; a state on
   the thing.

What I would not accept is the flag shipping with the limitation living only in a
commit message. You flagged it yourself, which is why this is a confirmation
rather than a catch.

## 3 · The write-path find

*A run that found nothing wrote nothing, so the reader kept serving the previous
run's positive forever.* That is the best bug in this project's history of this
class, because the surface symptom was a **sentence contradicting itself in one
paragraph** — today's *no advisories* above a twelve-day-old *PYSEC-2026-2132* —
and the cause was four words at the top of a writer.

The opt-in-per-writer shape is right, and excluding `architecture_recovery`
because its several steps write the same scope at different times is exactly the
distinction that makes an opt-in better than a global rule.

One thing to add, and it is the retraction rule rather than a new idea: **the
three repos carrying a stale positive beside a fresh zero should be corrected on
the record, not left to self-correct on their next run.** A contradiction that
resolves quietly leaves no trace that the interface once asserted both, and the
people who saw it have no way to know which half was wrong. Same instinct as the
correction you put in the vendored PR body rather than fixing silently.

## 4 · The freshness price, with its basis

*"A re-run costs about 1m 12s (median of 2 runs)"*, and *"declared 'fast' — not
yet measured"* where there is no measurement, is my §6 ruling generalised
further than I put it — you made **basis** a property of every figure rather than
of the one prior. Take that as the pattern for the ladder too: every cell carries
*measured (n)* / *declared* / *unknown*, and the three never render alike.

Which also answers a question I did not ask well. With basis on every figure,
the ladder can publish before §1 has weeks behind it: a table of mostly
*declared* cells is honest, and it is visibly a table waiting to be measured.
