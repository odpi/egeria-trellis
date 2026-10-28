# Funnel cost — the rulings you asked for, and the one finding that changes the drawing

**Replying to:** `FUNNEL-COST-MEASURED.md`
**Date:** 2026-09-13 · **Against:** `Funnel Cost Measurement Spec.dc.html` rev 2

This is the best measurement document this project has produced, and the reason
is one decision: **the 291 stay in the denominator.** *We cannot say, for 44% of
the rows* is a finding. *No repo reached Analysis* would have been a fabrication
with the same arithmetic behind it. Everything below assumes that discipline
holds into the drawing, where it is much easier to lose.

---

## 1 · The ladder — your ranking stands, and my spec was wrong twice

**Assessment and analysis as peers: yes.** The ladder was never a ranking of
cost or of quality; it ranks **commitment** — how much of someone's attention
and money a resource has been given. Rule 17's axis puts scouting below both
because scouting *collects* and the others *reason over what was collected*.
Between assessment and analysis there is no axis, and inventing one to get a
tidy 1-2-3-4 would be a number that sorts rather than a number that means.
`scouting=1 · discovery=2 · assessment=3 · analysis=3`.

**`understanding` comes out of the ladder entirely.** I ranked a rung nothing
can stand on. The distinction that matters, and that the drawing must carry:
`enrichment` and `automate` have zero catalog entries **by design** (rule 17 says
so — they are served elsewhere), whereas `understanding` having zero is
**unexplained**. Those are different zeroes and this project has a rule about
that. So: drop it from the ladder, and put the fact somewhere it can be seen —
*understanding · no analyses in the catalog · not costed* — rather than leaving
an empty rung that reads as a tier everyone drops out of.

Your test that fails if an `understanding` analysis ever appears is the right
instrument. Keep it, and let its failure be the thing that re-opens this.

## 2 · Stop calling it a funnel

**More repos reach analysis (25) than scouting (22).** A shape that widens as it
deepens is not a funnel, and drawing it as one makes the reader's eye correct
the data rather than read it. The metaphor was mine and it is now the least
accurate thing in the spec.

Draw **reach per tier** as four independent bars against the honest denominator,
each carrying its own *cannot say* band — 291 of 660 is not a footnote, it is
nearly half the ink. Then the inversion reads as what it is: a fact about which
repos got measured, not a leak in a pipe.

And drop the retention framing with it. *discovery → analysis, 18 of 18 (100%)*
is not retention; it is **"everything we can see deeply, we saw deeply"** —
which is a statement about the visible set, not about behaviour. A 100% in a
retention column will be quoted as a success by someone who did not read §0.3,
and it is the opposite.

**The selection effect goes in the headline, not the caveats.** The 26 visible
repos are the recently-surveyed ones — exactly the ones most likely to have gone
deep — and the 63 invisible ones are the older, shallower ones. The bias points
the same way as the finding, which is the dangerous direction. So the sentence
is: *on the 26 repos with step recording — the recently surveyed ones, which are
the most likely to have gone deep — depth does not narrow.* One sentence, above
the chart, not under it.

## 3 · The real finding is §2 + §4 together

Not *"everything goes deep"* but **"depth is not driven by the keep/stop
decision."** Eight of sixteen terminal decisions had no attributable run before
them; the deep runs happened on whatever was convenient to survey. That is a
product finding, and I agree with the coverage audit's remedy — **keep
investigating should schedule the deeper surveys** — with one design condition.

**It offers; it does not silently queue.** *An action must name what it would do,
before it does it*, and this is the most expensive action in the system. So at
the moment a verdict of `investigating` or `tracking` is recorded, the same
preview component that serves *Run across N* appears with what depth would cost
on this resource, and the person presses or does not. A verdict that quietly
spends a hundred seconds of somebody's compute is a verdict people learn not to
record.

The corollary is the honest one: if they mostly decline, the finding is not
"the funnel is broken", it is "depth is not worth its price here" — and that is
worth knowing too. Instrument the decline.

## 4 · Rationales — leave the asymmetry alone

`abandoned` 2 of 3 and `ignored` 1 of 1 carry a reason; `recommended` 0 of 4 and
`using` 0 of 8 carry none. That is not a gap to close. People explain why they
stopped and not why they continued, in every system anyone has ever built, and a
required box would produce *"looks good"* eight times.

Where the absence actually costs something is the reversal — a `using` repo later
abandoned, with nothing on the record about why it was adopted. So: **prompt for
a reason when a verdict reverses a terminal one, never on a first verdict.** No
data for that case yet, as you say; the prompt should exist before there is.

## 5 · Databases and filesystems have no disposition — say so where it is missed

*You can only record a verdict about a repo* is a finding, and it should be
visible in the place someone goes looking: on a non-repo, the disposition row
reads **no verdict can be recorded for a database yet** rather than rendering an
empty chip set. An empty facet row is indistinguishable from "nothing is
dispositioned", which is a claim about the data rather than about the mechanism.

**Decision (project owner, 2026-09-23): superseded.** The mechanism gap this
section named closed on 2026-09-22 (PR #223 generalized `repo_dispositions`'
PK to `(entity_type, entity_slug)`) — a database/filesystem CAN carry a real
disposition now, the same as a repo. The apology text above was left in
`/next`'s sidebar for another day past that, rendering "no verdict can be
recorded" over a disposition that had actually been recordable since PR #223
— itself the "confident wrong answer" this whole document exists to prevent,
just inverted (an apology where there is now a real answer, rather than an
empty chip row standing in for one). The project owner confirmed disposition
facets, groups and multi-select bulk actions are wanted for all three
resource types (re/multiselect-groups-dbfs, 2026-09-23); the sidebar's facet
row is unconditional now and this section's guidance no longer applies.

## 6 · The interim prior — yes, and mark it declared

Do the five minutes of YAML. But it renders as a **declared** number, visually
distinct from a measured one and labelled as such — *declared `run_time`, not
measured* — because a declared figure sitting in a cost chart that looks measured
is precisely the failure this spec was written to prevent. When §1 has weeks of
`runs` behind it, the measured value replaces it and the label changes; until
then the prior is a placeholder that says it is one.

## 7 · The instrumentation — right, and one addition

Cold / warm / not-consulted as **three states** rather than "bytes fetched" is
the same move as `□` versus `?`, and it is correct. `record_uncounted()` so a
zero says which zero, likewise. The ContextVar trap is worth the line it got.

The freshness gate is a good call and the UI surfacing the skip with the age is
right — with one addition, since the subject is cost: **the skip says what it
saved.** *Skipped — the result is 12 minutes old; a re-run costs about 70s ·
run anyway.* A gate that only says "too fresh" reads as an obstacle; one that
names the price reads as the system being careful with your money.

---

## What I would not spend time on

§3b and §5. You are right that §5 changes no decision on its own, and the
coverage audit answered the sharper form of §3a. The backfill is the only thing
on your list that would move the numbers materially, and it cannot be done from
the rows — so the honest position is that **26 of 89 is the permanent visible
set for everything before step recording**, and every chart says so for as long
as that is true.

Separately: `tier_resolution.py` as committed code with 18 tests, rather than a
script in a scratchpad, is why this reply could be short. The measurement is now
a thing that can be re-run and disagreed with.
