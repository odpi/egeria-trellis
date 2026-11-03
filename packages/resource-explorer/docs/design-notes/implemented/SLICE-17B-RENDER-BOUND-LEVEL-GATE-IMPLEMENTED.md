# Slice 17b: bind the level gate to what renders, not to catalog capability — implemented

**Coordinator brief:** Phase 1b, slice 17 follow-up (`re/coordinator-brief-phase-1b`).
**Replying to:** the live signed-in gate on `coco_pharma` for PR #293 (slice 17),
which did not pass.
**PR:** #TBD (`re/slice17b-render-bound-level-gate`).

## What the gate found

Slice 17's `FactLayer._check_level` withheld the checkmark (design §18.3)
when every known fact answering a sub-`resource`-level question came from an
analysis whose `analysis_catalog.yaml` entry declares
`target_shape: whole_resource_only`. Live against `coco_pharma`:

- (a) and (b) passed: Coverage Signals / Subject Signals / Preliminary Fit
  run correctly, and "Is this database alive" is correctly demoted with its
  reason.
- (c) failed: "Which schemas carry the data, and which are system, empty or
  staging?" (levels: `container`) stayed **ticked**, with the rendered
  answer reading "table count 56 · column count 427 · catalog only table
  count 53" — no schema named anywhere.

**Diagnosis:** `schema_inventory` declares `target_shape: single_container`
— it genuinely stores one row per table, `schema_name` included
(`_schema_inventory_results` in `survey_definition_adapter.py`). The old
gate read that as "capable of answering at container level" and stopped
there. But `schema_inventory` has no `headline_reader`
(`DATABASE_ANALYSIS_HEADLINE_MAP`), so the frontend's `readEnvelope`
(`app.js`) falls to its rung-3 fallback, `scalarMeasures()` — which
explicitly skips every list/object-typed field by design (the whole point
of a headline_reader is to say something in words about the shape
`scalarMeasures()` can't). The result: `tables` (with every `schema_name`)
never reaches the screen, no matter what the catalog declares the analysis
capable of.

## The fix

`FactLayer._check_level` (`facts.py`) now requires a known fact to have
produced a non-empty `headline` before counting it toward "answered at
level" — the one rung that can carry member-naming prose past
`scalarMeasures()`'s fallback. `target_shape` is still consulted, but only
to choose the wording of the note when the gate fires:

- No known fact is `target_shape`-capable at all → the old wording:
  "Answered only as a whole-resource rollup..." (nothing to show).
- A known fact *is* capable (declares `corpus`/`single_container`) but
  produced no headline → new wording: "...stores per-{level} rows, but no
  reader shows them yet." — naming the exact analysis a future
  `headline_reader` (or slice 22's schema/table view) needs to cover.

One known fact with a real headline is still enough to satisfy the level,
same conservative rule as slice 17 — `row_count_snapshot` has one, so
"How big is this database" (which mixes `schema_inventory` and
`row_count_snapshot`) correctly stays ticked while "Which schemas carry the
data" (which only cites `schema_inventory`) is now correctly demoted.

## Explicitly NOT done here

Two further defects the same gate screen surfaced, logged for separate
follow-ups rather than folded into this fix:

1. **Two denominators, unlabeled.** The credential banner reads "SELECT on
   3 of 61 table(s)"; the size answer reads "10 of 56 tables measured" —
   the probe (`_credential_scope_status`) and the inventory
   (`_schema_inventory_results`) count different underlying sets (views?
   system tables?) and neither says so. Needs its own investigation into
   what each actually enumerates before deciding whether to reconcile the
   totals or just label the difference — not a same-shape fix as the level
   gate.
2. **Check (b) unverified.** The gate report confirmed Coverage Signals /
   Subject Signals / Preliminary Fit show Run, but the two "not run" rows
   were below the fold and not explicitly scrolled to. Needs re-confirming
   on the next live pass, not a code change.

Also **not** attempted: teaching `_check_level` to inspect `Fact.value`
directly for level-appropriate structure (e.g. "does `tables` contain a
non-empty `schema_name`"). That would be a second, independent path to the
same conclusion the frontend's own rendering already settles via
`headline` — binding to `headline` keeps exactly one source of truth for
"does this reach the screen," rather than the backend re-deriving a guess
about what the frontend will do with a value it hasn't rendered yet.

## Tests

- `test_slice17_question_level_gate.py`: 2 existing tests updated (a
  per-member/single-container analysis now needs a written headline to
  satisfy the level, not just a capable `target_shape`) + 5 new (the exact
  regression: a capable-but-headline-less analysis still gates, with the
  right note wording; one headline among several facts is still enough;
  the real catalog end-to-end confirms `schema_inventory` has no
  `headline_reader`, "Which schemas carry the data" gates, and "How big is
  this database" does not).
- Full suite: 6374 passed, 103 skipped, 1 failed — the same pre-existing
  `test_egeria_live_smoke.py` live-Egeria-environment failure noted on
  slice 16 and slice 17, unrelated to this change.

## Live signed-in gate

**Not yet run.** Needs a real signed-in session against `coco_pharma` to
confirm, on screen:

- "Which schemas carry the data, and which are system, empty or staging?"
  is now **un-ticked**, with a note that `schema_inventory` stores
  per-schema rows but no reader shows them yet.
- "How big is this database — schemas, tables, views, columns, rows and
  bytes?" is still **ticked** (must not regress).
- "Is this database alive..." stays demoted as it already was (must not
  regress).
- Coverage Signals / Subject Signals / Preliminary Fit still show Run with
  no "no mapped survey step" text (must not regress — same regression
  slice 17 fixed, unrelated to this change, but worth a glance since it's
  the same screen).

Whoever runs this: append the outcome here, one sentence per screen, per
the coordinator brief's own gate convention.
