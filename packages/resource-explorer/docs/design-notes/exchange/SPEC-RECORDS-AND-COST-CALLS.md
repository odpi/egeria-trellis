# The report record, buildable — and the two calls you handed back

**Date:** 2026-09-13 · **Read against:** `main` at `2d4357e`
**Drawn at:** `design_handoff_list_answers/` — now seven artboards on two pages.
`RunChoice.dc.html` and `DepthOffer.dc.html` are new.

Everything ruled on shipped, including the condition — the drain creating the
evidence links *before* `queued` became reachable, rather than after. And the
CVE case verified from a real press rather than a fixture: the 2026-09-01 `click`
advisory superseded by a fresh zero, the row flipping five minutes later. That is
the whole doctrine working end to end on live data, which it had not done before
this week.

Below: the two calls you asked me to make, then the build spec for the three
sheets, so `SaveReport` / `Report` / `Records` stop being drawings.

---

## A · The `/next` one-click re-run — it stops being one click

**Ruling: the link opens a small popover anchored to itself, and nothing queues
from the link.** Drawn in `RunChoice.dc.html`.

Not because the choice matters every time, but because *an action must name what
it would do before it does it*, and this is the action whose price moved by a
factor of six while we were discussing it. The extra click is what buys the
sentence. **Background first**, because on this evidence it is right nearly every
time; the waiting option keeps its own name (*Run and wait*) rather than becoming
a cancel.

The popover carries the same four price variants the run dialog already has —
measured split, measured unsplit (labelled), declared word, not known — and both
buttons are offered in all four, including *not known*. A missing price is a
reason to say so, not a reason to withhold the action; the first run is what
fixes it.

**One correction to copy that shipped.** The freshness sentence currently reads

> about **1m 32s** to publish — about **1m 32s** in all

which states the same figure twice, because the run half rounds to a tenth of a
second. A total that restates its larger half verbatim reads as a bug in the
sentence. When one half is more than nine tenths of the total, name it instead:

> about **0.1s** to run and about **1m 32s** to publish — **1m 32s** in all,
> nearly all of it publishing.

General rule: **the total names its dominant half rather than repeating it.**

## B · §3 — keep investigating, and the price of meaning it

Drawn in `DepthOffer.dc.html`. At the moment a verdict of `investigating` or
`tracking` is recorded, the pane shows what that verdict does *not* do, names the
analyses at the analysis and assessment tiers that have never run on this
resource, prices them with the split from `estimate_run_cost`, and offers three
buttons: *Run these in background* · *Choose which* · *Not now*.

Three properties that are the design, not decoration:

- **The decline is recorded on the verdict** — the trail reads
  `investigating · just now · dwolfson · depth offered, declined`. A corpus of
  declines does not say *the funnel is broken*; it says **depth is not worth its
  price here**, which is a finding about the analyses. It cannot be read off
  anything if the decline leaves no trace.
- **Not a nag, not a gate, not a scold.** Once per verdict, in the pane, never a
  modal, never again for the same verdict; the verdict is already recorded when
  this appears and nothing waits on an answer; and *this verdict does not
  schedule anything* is a fact about the mechanism, not *you should run these*.
- **It does not appear on `abandoned` or `ignored` at all.** Those are decisions
  to stop spending, and offering to spend at exactly that moment is the one place
  this reads as an argument. `recommended` and `using` get it **only** when the
  repo has never been measured — adopting something nobody looked at is the case
  worth a sentence, and it is four of the eight the measurement found.

The unpriced analysis renders as `declared fast` in the table and is excluded
from the total, with the total saying so: *2m 1s in all across three;
`interface_surface` is not priced.* Never fold a declared word into a sum.

---

## C · The report record — build spec

### C1 · One table, two kinds

A report is Curate's commit record with no steps. If the commit record's table
can carry `kind`, use it; if its shape is too welded to steps, a sibling table is
fine — what must be true is that **both list together under the resource, newest
first, and neither is edited after the fact.**

A record row holds: `kind` (`catalogue` | `report`), `resource`, `name`,
`written_at`, `author` (server-stamped, no field on the write model), the
`analysis_id` and its `run_at`, the `facet` if the selection had one, `total` and
`shown`, and the rows themselves as a **snapshot of names with their detail** —
never a stored filter. Promotion's `provenance_line` composes the same sentence
it already composes; a report is its fourth destination.

### C2 · The act

`SaveReport.dc.html`. The selection footer gains *save as report* beside the
three existing acts. Two states:

- **A selection** — the footer already reads
  `16 selected · high, plus 1 added by hand · from cve_scan · 2d ago · a snapshot, not a query`.
  Add the fourth act; nothing else changes.
- **Nothing picked** — the footer reads `The whole list · 32 dependencies · …`
  and offers **only** *save as report*, with a sentence saying the other three
  need a selection. A report is the one act that makes sense on a list nobody has
  triaged, so it is the one act offered before anything is picked.

The name proposes itself from facet and count, server-side, and **a typed name
wins until cleared** — touched from the input event, never inferred from the
string. Anonymous: *Sign in to save a report — a record needs an author.*
Gated, not hidden.

### C3 · The record itself

`Report.dc.html`. The header sentence is the part that matters:

> **32 of 32** dependencies — nothing capped.

or, when the request capped it, *200 of 1,412 — capped by the request.* **In the
header, never a footnote.** A saved artifact outlives the person who knew about
the cap, which is the whole reason the rail sentence exists.

Under it, one provenance line: asked-as, the analysis and its run time, the
author and written-at, and *a snapshot, not a query*. Then the rows, grouped as
the member tree groups them, with any group's `truncated` flag surviving into the
record.

**Markdown and CSV are exports *of* the record**, carrying the same header
sentence and the same provenance line. The record is the thing; a file is a view
of it. This is the gap worth closing: every provenance line this project composes
so carefully currently lives on the clipboard and dies on the first paste.

The three acts apply to a report too — it is a thing you hand to someone — and
the rows travel with their provenance line, not as bare names.

**Append-only.** A report is never edited; a correction is a new record that says
what it corrects, exactly as Curate states about reversal. Until one is written,
a record whose evidence has moved says so on itself: *out of date — `cve_scan`
re-ran on 09-12 and two of these are no longer high. No correcting record has
been written yet.*

### C4 · Where they live

`Records.dc.html`. Under the resource, beside the journal and the verdict trail —
**not inside the journal**, which is prose testimony and which a thirty-two-row
table fights. A journal entry may cite a record; that is the link between what
someone thought and what was true when they thought it.

A catalogue record shows its steps and their outcomes inline, as Curate's pane
already draws them. A report record shows its header sentence. Same row grammar,
same date column, same author.

---

## Still open, and still mine

The component column in `/next` — accept/reject with ports derived — and the wire
diagram. Nothing above touches them.

And §1 is now a matter of waiting rather than of design: re-run it in a week or
two, when enough rows carry the split. The cells that cannot be split yet say
*not yet split* rather than inventing a number, which is the only thing that
needed ruling on.
