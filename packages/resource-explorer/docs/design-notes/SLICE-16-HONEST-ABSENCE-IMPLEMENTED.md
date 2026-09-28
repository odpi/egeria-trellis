# Slice 16 (part): honest absence states and the evidence-footer wall — implemented

**Coordinator brief:** Phase 1b, slice 16 (`re/coordinator-brief-phase-1b`).
**Replying to:** `REVIEW-SURVEY-PANE-285.md` (#286), and its own follow-up
correction on the first attempt at the footer fix.
**PR:** #291 (`re/slice16-honest-absence-and-evidence-footer`).

## What this covers

1. **`facts.py::_read_results`** no longer conflates "a reader ran and found
   nothing" with "no reader is registered at all." The latter is now its own
   state, `NO_READER` (`result_status.py`), excluded from `Fact.is_known` so
   neither a checkmark nor a "measured zero" claim is made. Classic's
   `_FACT_STATE_TEXT` table (`index.html`) carries the matching entry, since
   it renders `facts.py`'s states independently of `/next`.
2. **`chat.js`'s evidence footer** (`evidenceFooterListsHtml`) now renders a
   line only for a packed evidence section that has a real member reader.
   A readerless section — one, or a dozen — gets no line in the footer at
   all, corrected after a first attempt that combined them into one line
   naming every key, which the review correctly called "the same wall,
   shorter." The absence, when nothing in the answer has a reader, is
   carried instead as one generic clause on `sourceLine()`'s existing
   provenance sentence: "... · no evidence lists were available for this
   question" — no key names.
3. **Confirmed, not fixed here** (it never existed on this branch): the
   key-walking summariser the review also named for deletion
   (`build_result_summary`/`_generic_result_fallback`) was entirely part of
   #285's own `stage_page.py` addition and is gone with #285's revert
   (#289). Grepped directly against this branch to confirm.

## Explicitly NOT done here

Per the design/architecture session's own note that it is a separate,
non-blocking piece: wiring `credential_capability`'s stored probe into
`context_compile`'s evidence selection. This is why "What credential
capabilities do I have?" still returns no answer in chat — the probe result
exists and is real (verified live against `coco_pharma` earlier tonight),
but the compiled-evidence path that would surface it in a chat answer
doesn't reach it yet. Tracked as its own follow-up, not part of this slice.

## Tests

- `test_facts.py`: 5 new (no-reader vs. empty-reader distinction at the
  `_read_results` level; `Fact("x", NO_READER).is_known is False`; the
  classic `_FACT_STATE_TEXT` table carries the new entry with the right
  wording, not borrowed from `nothing_found`).
- `test_next_rail_states.py`: 2 rewritten (a readerless section, alone or in
  a group, renders nothing in the footer) + 1 new (a readerful section still
  renders correctly alongside readerless ones) + 3 new (`sourceLine`'s
  absence clause fires only when list-shaped evidence existed and none of it
  had a reader — not when no lists existed at all, and not when at least one
  readable list existed).
- Full suite: 6338 passed, 102 skipped, 1 failed — the pre-existing
  `test_egeria_live_smoke.py` live-Egeria environment test (independently
  confirmed failing identically on `main`, unrelated to this change).

## Live signed-in gate

**Not yet run.** Needs a real signed-in session against `coco_pharma` (or
another database resource) to confirm, on screen:

- An analysis with no results reader (e.g. `db_activity_signals` on a
  question like "is this database alive") shows the honest no-reader
  wording, not "measured zero," and carries no checkmark.
- A chat answer whose only packed evidence is readerless database analyses
  (e.g. asking a broad database question that pulls in `coverage_signals`/
  `subject_signals`/`grain_determination`) shows a clean footer with no
  per-section or combined "no member reader" lines, and the provenance
  sentence under the answer carries the one generic clause instead.
- A chat answer that mixes a readerful section (e.g. a repo's
  `dependency_analysis`) with readerless ones still shows the readerful
  section's real "open the full list" line.

Whoever runs this: append the outcome here, one sentence per screen, per
the coordinator brief's own gate convention.

**Gate result (project owner, 2026-09-25):** passed, merged. Owner's own
words on the live session against `coco_pharma`: "don't see any found
nothing screens."
