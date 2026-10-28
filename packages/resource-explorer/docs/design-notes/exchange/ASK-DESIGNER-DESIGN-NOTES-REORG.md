# ASK — Designer: the design-notes move, two questions (2026-10-01)

From the design session, at the owner's request, before anything moves.
Reply as `REPLY-DESIGNER-DESIGN-NOTES-REORG.md` in this folder. Short
answers are fine; this is housekeeping, not design.

## What the owner approved

`docs/design-notes/` holds 219 files in one flat directory, 103 of them
finished-work records. The owner approved "Option A" of the coordinator's
proposal:

- `implemented/` takes the 103 `*-IMPLEMENTED.md` notes (plus two fix notes
  without the suffix), and shipped SPEC/BRIEF/ASK/PLAN notes join them once
  the coordinator maps each to its implemented twin.
- `evidence/` takes 10 audits, probes, measurements and CSVs.
- The root keeps 73 files: INBOX.md, the handoff and triage notes, the open
  design notes, and **every REPLY-/REVIEW-/RULING- note you write**.
- No filename changes. `wireframes/` and `screenshots/` stay exactly where
  they are, so your export targets don't move.
- A permanent test fails on any dangling pointer to a note, and a root
  `TIMELINE.md` (one dated block per note, maintained by the coordinator)
  keeps each note's story even if it later leaves the tree.

Your write targets are unchanged: replies to the root, exports to
`wireframes/`. What changes for you is where implemented notes live when
you read them by path, and one edit to a file you own.

## The two questions

1. **INBOX.md's "answered" column.** It derives from the `*-IMPLEMENTED.md`
   names, and those files move to `implemented/`. Either the derivation
   scans `implemented/` and you re-point the existing links yourself right
   after the move merges, or the owner's one-time exception to the
   single-writer rule lets the move PR add the `implemented/` prefix to
   those links in INBOX.md. Which do you prefer? The design session's
   recommendation is that you apply it, so the file keeps one writer, with
   the exception as the fallback if you can't within a day of the merge.
   Either way, nothing moves until you say.

2. **Is INBOX.md still authoritative?** It was last reconciled 2026-09-17,
   and sixteen replies have landed since. The move PR won't touch its
   content. Do you want to reconcile it after the move, as a separate pass
   with the coordinator's TIMELINE entries as a cross-check, or retire it in
   favour of TIMELINE.md? The design session's recommendation is reconcile,
   because INBOX answers "what did design say and was it built", which the
   timeline doesn't.

One more, small: TIMELINE.md wants one line per note at the time it lands.
For your replies, would you add that line yourself, or should the
implementer add it for each reply as it lands? Either is fine; say which.

## Timing

The move lands only after the work-lists and investigation-lens slices
merge, so the freeze is short. Nothing moves or freezes until your reply.
