# The stage page — the owner's round, seven faces of one question

**Replying to:** `OWNER-ROUND-2026-09-14.md`, points 1, 2, 3, 6, 7, 9, 10 and the
two S3 questions
**Date:** 2026-09-14 · **Read against:** `origin/main` at `0841c17` (after `#89`)
**Drawn at:** `design_handoff_list_answers/` page four — `StagePage`,
`FactInPlace`, `AnalysesIndex`

The owner says *the question focus is right; the facts are hard to find*. The
code says why, more sharply than the complaint does, and it is worth stating
precisely because it decides the whole round:

**The Questions row renders the answer and cannot reach the members at all.
`Dashboard · by question` renders the same sentence from the same envelope so
that it can offer a `detail` disclosure whose payload opens in a 290px rail.**

So the answer is in two panes and the fact is in neither: four moves, one of them
a pane change, to see a number the row already summarised. Seven of the ten
points are that, from different sides.

---

## The spine

**One object: the analysis. One entry: the question. One place for the fact: the
pane, under the answer.**

### What a stage page duplicates today

Worth listing, because point 6 is not a tidiness complaint — each is a second
place the same fact can be wrong in, and this project has spent two weeks finding
that class of defect in the data:

- **the answer sentence** — Questions row, Dashboard by-question head, the
  evidence rail's headline list: one string, three surfaces.
- **findings and counts** — under the question, again under the analysis, a
  third time in the rail.
- **disagreements** — computed twice from two sources, emitting the identical
  sentence.
- **descriptions** — first sentence on the survey row, the whole thing again
  behind *definition history*, the analysis's own description a third time on the
  dashboard section.
- **`re-run`** — three call sites, three interfaces: the priced popover, an
  unpriced dialog link, the batched depth-offer table.
- **perspectives** — on every Questions row, wrapping; absent from Survey and
  from Dashboard by-analysis. The inverse duplication.

### The sub-tab strip after the fold

`Find repos · Questions · Survey & analyses · By analysis · Disposition`

1. **Questions absorbs `Dashboard · by question`.** Same answer, same envelope,
   one pane. The row gains the disclosure.
2. **Survey becomes *Survey & analyses*** — definitions *and* the analyses they
   run (point 1).
3. **`By analysis` keeps its own tab**, because reading a repository one analysis
   at a time is a real second question. The in-pane toggle goes: a view that is
   also a tab is a tab. *Measured, but no question asks* moves here with it — it
   is a statement about analyses and belongs beside them.
4. **The rail keeps provenance, and only provenance.** *Evidence* — what produced
   this, when, from how many measurements — is narrow by nature. Rows are not.
   The code already admits why members went there: the Analysis pane *"held an
   empty ask box and eighteen hundred pixels of nothing."* The rail was chosen
   because the pane was empty; the pane is no longer empty.

---

## Point 10 · the fact opens under the answer

`FactInPlace.dc.html`. One new link, last in the provenance row:

> `language_file_classification` · run 2m ago · evidence · copy as evidence ·
> re-run · **the numbers behind this 6 ›**

It says what it opens and how much of it there is. Not *detail*, which names a
gesture rather than a thing; not *members*, which is the data model's word for
rows a person calls numbers, files or packages.

Open, it is a **table of six measurements** — name, value, and what each one
opens — indented under the answer, in the pane, the centre column keeping its
place:

| measurement | value | opens |
|---|---|---|
| `lines_of_code` | 175,716 | — |
| `source_files` | 965 | the 965 files › |
| `languages` | 7 | the 7 languages › |
| `largest_language` | Python · 71% | — |
| `generated_files` | 12 | the 12 files › |
| `binary_files` | 0 | a zero stays a zero |

with a footer: *Read from `language_file_classification`, run 2m ago · vendored
and generated paths excluded · save as report.*

Three rules in that:

- **The numbers come first, the rows come next.** 965 file names are not the fact
  behind *965 source files* — the number is. This is what the owner could not
  find: not a list, a table of six.
- **Rows still go to the rail, and now they earn it.** *the 965 files ›* opens
  the members rail exactly as today. A column of mono file names is the one thing
  290px suits. Counts open what they counted — one layer down, not one pane over.
- **Every state keeps its sentence.** A metrics-only analysis says *records
  measurements, not members* where its *opens* cell would be — `#89`'s fix moved
  one surface out, so the promise is never made. An unset renders *not
  recorded*; a zero stays a zero.

**What this deletes:** `Dashboard · by question` exists to host this disclosure.
Once the disclosure is on the row, that view is the answer rendered a second time
for no remaining reason. Its parts have homes — the *N questions at this stage*
summary on the Questions header, which already counts answered and loading; the
disagreements under the question they dispute, where one of the two
implementations already puts them.

---

## Points 1, 2, 3 · Survey & analyses

`AnalysesIndex.dc.html`. Two granularities, one index.

**Definition rows** carry the thing that settles point 2: **how many of their
steps fetch.**

> Repo Architecture Discovery · **4 steps · 2 fetch** · never run · Run →
> Repo Discovery Survey · **7 steps · none fetch** · ran 3d ago · Run →
> Repo Refresh · **14 steps · 9 fetch · all tiers** · ran 11d ago · Plan →

Those first two sit under one heading and read as peers; one fetches twice and
the other not at all. That is the axis the tiering itself turns on, and the only
thing a person needs before pressing. `requires_resources` is already on
`StepInfo`, so the count is a sum the row can carry.

**Analysis rows** carry the four facts that make a row pressable — does it answer
anything, when did it last run, what does it cost, can I run it — with the state
glyph from the existing set and `recommended` as a pill:

> ✓ `language_file_classification` · *what it does* · 2 questions › · 2m ago ·
> 0.2s · 80s publish · re-run
> ○ `secret_scan` · *what it does* · **recommended** · no question asks · never
> run · declared *fast* · run

Sorts, never filters: *by name · never run first · by what it costs*.

**Point 3: the description is a popover.** `secret_scan`'s is 700 characters and
it is the *short* half of what that analysis says about itself; nine of those on
a page is a page nobody reads. The popover carries the prose plus the catalog
facts — stage, declared run time, availability, perspectives, ruleset link.

One thing to notice while moving it, though: that description is the most careful
writing in this project, and part of it is not about the analysis at all. *Never
claims "no secrets": only "no matches against this ruleset, in the current HEAD
snapshot of tracked files"* is **a caveat about the answer**, and it belongs
wherever the answer appears — the caveat line under the question, not only in a
popover on another tab.

---

## Points 6, 7, 4 · the clauses

**6 · Other stages.** Two different facts render identically today. Either
*these matched this stage's questions and belong to discovery 2 · assessment 1 ·
analysis 1*, or the stage filter did not resolve and the route returned every
definition for the technology type — in which case: *the stage filter did not
resolve · showing all 11 for this technology type*. A fast path must not lie, and
the full-scan fallback is a fast path lying quietly.

**7 · Perspectives.** Every pane either filters by them or says it does not.
Survey **sends** them — the route already accepts and honours them and `/next`
simply never passes them. `By analysis` says *not filtered by perspective*. And
nothing-held renders as **no perspective held · everything shown**, never as a
filtered state that happens to match all: today `Perspective · 0 of 12` with no
residue line is indistinguishable from a filter that matched everything.

**4 · Visuals — yes, under one rule, because of why they went.** A tile read
*Health 82/100* while the card beneath it said *82.2*. So: **a visual may only
show a number the row beneath it also shows, from the same value, rounded the
same way.** Under that rule the radar can come back. Without it, it will disagree
with its own caption again — and the removal comment is right that the disagreement
was the defect, not the chart.

---

## The two S3 questions

**Software Library vs Software Capability.** The owner is right and this is a
real Egeria-type question, not a copy fix. `manifest_parse` maps one Python
distribution to one `Software Library` — true of `trellis-context`, false of
`egeria-advisor`, which is an application. The distinction Egeria offers is
`Software Capability` for something that *does* something deployable. So the
proposal should come from **evidence of deployment** — a console entry point, a
Dockerfile, a compose service — and the row should name the evidence that made
the call:

> `egeria-advisor` · **Software Capability** · a console entry point and a compose
> service
> `trellis-context` · **Software Library** · an importable distribution, no entry
> point

And on granularity: nine packages in a monorepo are not nine catalogue entries.
A package with no deployment evidence and no consumers outside the repository
gets **probably not catalogued** — marked, not hidden, and unticked by default,
so the person confirms nine rows rather than composing them.

**The members rail's legend.** A checkbox with no visible act is the defect the
owner names; the acts exist but only appear once something is picked. Two fixes:
the footer states them before anything is ticked — *pick rows to add them to a
work list, raise an RFA or note them* — which it already does for reports, and
the detail column gets a header naming what it holds: *version · scope*, so
`>=5.0.0 optional` reads as a constraint and a scope rather than as two words.

---

## What I am not doing in this round

**Point 8, the untouched panes** (Investigation, Activity, Admin, RFAs, Work
lists, Automate) — scope, and yours to prioritise. The deferred-pane pattern
already marks them honestly, which is the only thing design owes them until they
are scheduled.

**Point 5's copy** is the `/next` session's, and the third case is the owner's own
ruling of 09-11: an analysis also serves the chat's ad-hoc questions, so it is
not orphaned — the catalog simply cannot say what it serves. Three cases, not
two. The analyses index above carries the third.

**And the ports round two** — `detect` and `coupling` — is now behind this. I
still want it settled before anyone accepts at scale, because a verdict keyed by
scope lands on both proposals and every verdict recorded under that ambiguity has
an unclear subject. It is a small ruling, not a round; I can take it in parallel.
