# Corrections implemented — and a fifth instance found by changing the sample

**Status:** implemented. Next UI corrections round, 2026-09-10; kept as a record.

**From:** the implementation session, 2026-09-10
**Against:** the Corrections section of
`Resource Explorer Survey and Dashboard.dc.html`
**Branch:** `re-experimental-ui`, pushed through `8aeb28a`

Both corrections taken. Two details were missed on the first pass and caught
on a re-read; they are recorded below rather than quietly fixed, because the
reason they were missed is worth more than the fix. And the round ended with a
defect that none of the design work would have found — it took changing which
repositories we look at.

---

## The third trend state — built as specified

`series` · `first measurement` · `not tracked`, and the difference is a fact
about the analysis rather than about the resource.

**Declared, not discovered.** `REPO_ANALYSIS_RESULTS_MAP` already either
registers a trend reader or registers `None` deliberately, so that is now on
the analysis descriptor — `trend: tracked | not_tracked | unknown` on
`/api/analyses/{resource_type}` — and the UI never asks. Your line is the one
I kept: *a 400 is the right answer to a wrong request; the fix is not making
the request.*

Across the repo catalog: **19 tracked, 15 not tracked, 2 unknown.**

`unknown` is deliberate and separate: not in the results map at all is not the
same as declared untracked, and collapsing them would be the guess this change
exists to remove.

**Two things I got wrong first time**, both from working off a summary of your
corrections rather than the document:

1. *"No history section rather than an empty one"* was implemented as no
   section **and nothing else** — which leaves a reader who expected a history
   unable to tell "correctly none" from "we forgot". Your wording is now
   there, flat and once: **"Current-state classification — not tracked over
   time."**
2. `first measurement` said only "there is no trend with one point". Your
   definition carries the clause that does the work — **"Tracked, but measured
   once — there will be a trend; there isn't one yet"** — and that second half
   is precisely what a reader cannot infer from an empty list.

Worth naming the mechanism: a summary of a correction loses the *wording*
first, and in both cases the wording was the correction. The substance
survived the summary; the sentences did not.

## The run link — a list, labelled as one

"Runs on this resource", with no claimed link to the value it was opened from.
Your framing of it as rule 1 applied to a link is the one in the code comment.

The run id on the results rows is on the backlog as a **persistence change,
argued as provenance** — with your sentence as the argument, because it is
better than mine: *a measurement that cannot name what produced it is not
fully provenanced, and provenance is the thing that has found every real
defect in this project so far.* The entry also records why timestamp
correlation must not be built instead: the two clocks it would correlate
disagreed by weeks on the same analysis.

## "Unchanged across N runs" — in every plan, not one

You were right that it earns a place in the plan preview, and right that it is
the same component already unified — which is exactly what the first pass
missed. It was in the survey plan only. It is now in all three. Live, on
*Bring up to date*:

> Of the **22** run(s) this queues, **8** are for measurements unchanged
> across their recorded runs — `foss_scorecard`, `chaoss_metrics`. Re-running
> those will measure them again and, on this evidence, change nothing.

The 22 is a coincidence.

It fills in after the plan renders, never blocks it, samples at most twelve
pairs, and counts nothing it could not read — a plan preview must not cost
more than the run it describes.

**One deliberate divergence.** You wrote "unchanged across their *last 3
runs*"; this says "across their recorded runs". A window would be a third
threshold to defend on top of the two already marked as placeholders, and it
should come from wherever those eventually come from — Automate's cadence —
rather than being chosen twice.

---

## The fifth instance, and it took a different sample to see

The project owner pointed out that kafka — which every screenshot and every
verification in these rounds has used — is a different class of thing:
fundamental infrastructure, 1.2M lines, 33k stars, 349 contributors. The
repositories this tool is actually pointed at are docling, egeria,
egeria-workspaces. Re-verifying on those found this immediately.

**`survey_history` reported "nothing recorded yet" — on every repository.**
kafka, docling, egeria, egeria-workspaces, milvus, amundsen, polaris, sqlglot,
unitycatalog, marquez. Ten for ten is not a fact about any resource.

The endpoint returns `{dates, total_files}` — raw series, no `data` array —
because the *current* UI builds that figure client-side. Every other chart
returns a Plotly figure. `/next` read `fig.data`, found no traces, and
rendered an empty state, while the registry held ten points per repo:
**6,107 → 6,423 files over a month** on egeria-workspaces alone.

A fact about the **endpoint** rendered as a fact about the **resource**.

The part worth your attention is where it hid. It hid in *an honest-sounding
empty* — the state we have spent several rounds deliberately adding, because
absence must be a state and never a blank. I read it as the feature working.
An empty state is a claim like any other, and it is the one claim that looks
like diligence when it is wrong.

I do not think that needs a new rule so much as an amendment to rule 8: **an
absence that holds for every resource is a bug report about the reader, not a
finding about the corpus.** Cheap to check and it would have caught this on
the first repo.

The endpoint is unchanged — `/` depends on that shape — and `/next` adapts in
its chart layer, where the assumption lived.

## Calibration, going forward

Two things the sample was hiding, beyond the bug:

- kafka scores **100 activity / 100 community**, so the Dashboard looked
  uniformly healthy and the composed score never varied. egeria-workspaces is
  **49.8 — activity 100, community 11, release cadence 0, freshness 88**, which
  actually exercises the display you designed.
- kafka is the *sparse* one for star history — 4 points against 10–12 on the
  others. So it was atypical in both directions at once: enormous in code,
  thin in recorded series. A bad calibration target for a tool whose whole
  subject is recorded history.

The symbol-count disagreement does reproduce on egeria-workspaces — 26,563 /
27,092, 2.0%, the same unique-key shape — so that finding generalises rather
than being a kafka artifact.

Verification screenshots from here on will be on docling, egeria and
egeria-workspaces.
