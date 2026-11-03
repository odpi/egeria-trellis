# BRIEF — Native Egeria survey launch and read-back (2026-09-30)

From the design session. Dispatched by the coordinator on 2026-09-30 in its
own worktree off main (cd778f17). The owner's ask, verbatim from the E1
round: *"When are we going to also enable the egeria surveys to be run?"*

## The slice

On Survey & analyses, a survey that Egeria's survey action engine can run
for this resource kind shows a Run control that submits an engine action
through pyegeria. Prefect is not in this path.

Every status word on the row derives from persisted proof, never from the
branch the code took (see `feedback: status words derive from proof rows`):

| word on the row | proof it needs |
|---|---|
| submitted to Egeria · *time* | engine-action GUID stored on the `step_runs` row |
| running · read *time* | engine action status read back from Egeria, with the time it was read |
| complete · read *time* | as above, plus the survey report GUID stored and the report read into RE's findings |
| *Egeria's own status word* · *message* | Egeria's status and message on a failed action; never "complete", never blank |

Rules that apply:

- No agent signs in to RE. Dispatch is proven by reading the engine action
  back, read-only, through pyegeria.
- The write to the dev Egeria is an ordinary shared write (owner ruling
  2026-09-13); no quiet-window coordination needed.
- A second run of the same survey never duplicates findings. Read
  `reference: egeria dedup and uni/multi-link patterns` before touching the
  outbox; read `reference: ContextVar identity in threads` before starting
  any thread.
- Surveys RE cannot run for this kind say why on the row, not ○ not run.

## Owner's gate, on coco_pharma then adventureworks

1. Survey & analyses lists the Egeria-native surveys for a database with
   Run; those RE can't run for this kind say why, not ○.
2. Run submits; the row says "submitted to Egeria · *time*", and the engine
   action exists in Egeria (UI or pyegeria) with that GUID.
3. The row moves to complete on its own, showing when it was read, and the
   findings tab shows the survey report's annotations with the report's
   time.
4. Running the same survey twice does not duplicate findings; the second
   report replaces or is dated beside the first, whichever the existing
   survey rows do.
5. A survey Egeria fails shows Egeria's status word and message on the
   row, never "complete" and never blank.
6. Nothing on the Prefect path changed: a whole-definition run still
   stores a flow_run_id.

Merge on green after all six pass. Serve reports carry the provenance
proof (worktree HEAD equal to the tip, plus the process's cwd in that
worktree).

## Sequencing around it

After #363 (E1 Context tab) merges: the license spelling sweep (grep gate
only), then this slice and E2 in parallel in separate worktrees (this
slice: run route and Survey & analyses; E2: `stages/context.js` and
doc-sources). Whichever lands second merges main first. E3 follows E2; the
database-analysis batch waits behind all of them.
