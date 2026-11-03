# ASK — Designer: words when a cancelled step's report is kept (2026-10-18)

From the RE operations session, for the designer session, at the owner's request ("ask the designer for the
'report is kept' words in parallel"). Please reply as `REPLY-DESIGNER-CANCEL-REPORT-KEPT.md` in this folder.
Background: your `REPLY-DESIGNER-ENGINE-HOST-CANCEL-WORDS.md` (merged #733) and Cancel run as built in #734.

## What the live test showed (2026-10-18, coco_pharma, Database Scouting Scan)

The owner approved a live cancel test. Egeria's side worked: the cancel was recorded within seconds, and step 3 never
started. But your words assumed that a step cancelled mid-run "finishes but publishes nothing", and the live run broke
that assumption:

- Step 2, `postgres_operations`, **creates its survey report 3 seconds after it starts**, then spends about a
  minute filling it in. Most of a step's running time is spent publishing, so a mid-step cancel almost always lands
  after the report exists.
- RE keeps that report (deleting half-written evidence would be worse) and sends Egeria no completion.
- The card meanwhile said "⊘ cancelled · step 2, postgres_operations, is finishing and will publish nothing", which
  was untrue. When the run ended, it said "⊘ cancelled by peterprofile · 07:40 · 0 of 3 steps finished", with no
  way to reach the report that was kept.
- The run history under "runs · N ›" listed the run as "failed".

The builder now records when publishing starts, so the card can tell the two cases apart from a proof row. It needs
your words for the second case.

## Placeholders in the build now

| State | Placeholder |
|---|---|
| Confirm, when the running step has already started its report | (unchanged today) `Cancel this run? Step N, <step>, finishes but publishes nothing, …` |
| Cancelled, step still finishing, report already started | `⊘ cancelled · step N, <step>, is finishing · its report is kept` |
| Done, a cancelled step's report was kept | `⊘ cancelled by <user> · HH:MM · K of M steps finished · step N's report kept ›` |
| Run history entry for a cancelled run | `cancelled` (muted, not a fault) |

## Questions

1. **The confirm.** When the press comes after the running step has started its report, the confirm's "finishes
   but publishes nothing" is already false. What should it say then? Should the confirm vary at all, or say one
   thing that is true in both cases?
2. **Mid-step, report started.** Words and cue for "cancelled in Egeria, step still finishing, its report is being
   kept".
3. **Done.** How does the done line show a kept report from a step that did not finish? Is it a link like a
   finished step's "step N report ›", or something set apart? Is the report complete or partial? (RE lets the
   publish run to its end, so it is complete for the work the step did.)
4. **Run history.** The word for a cancelled run in "runs · N ›", next to "complete", "partial · 2 of 3 ran" and
   "failed".
5. **Anything else** the live run suggests: for example, whether a cancel cue should appear on the step itself
   ("step 2 ⊘ cancelled") as well as on the run line, which is what the card does today.
