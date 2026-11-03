# DESIGN — Quality Q3: the DatabaseQualitySurvey across a work list, priced, and on a schedule (as built, 2026-10-28)

Slice Q3 of `DESIGN-QUALITY-AT-SCALE-AND-TESTGEN.md` (§2.2, §2.3, §3). The project owner asked on 2026-10-28 to
build it on the design questions already answered. This note records, for each question the slice raises, the answer
and where it is recorded. An answer marked **assumed** is the option the design or the code's own conventions point
to, taken because no ruling was found. Each assumed answer is the owner's to confirm.

Gate (§3): a work list of coco_pharma and adventureworks runs as one batch with two priced rows, and a second
scheduled run skips the unchanged database and says so. The test for it is
`tests/test_quality_q3_batch_schedule.py::test_the_gate_two_priced_rows_then_a_scheduled_run_that_skips_the_unchanged_one_and_says_so`.

## What was built

- `resource_explorer/quality_batch.py`: the price line, the skip-unchanged rule, the batch (one
  `survey_definition_run` per database) and the scheduled firing.
- `POST /api/quality-batch/price` and `POST /api/quality-batch/run` (`web/routes/quality_batch.py`). The price
  reads only stored rows and queues nothing.
- The Survey press's enqueue is factored into `survey_definitions.enqueue_definition_run`, and the batch calls it once
  per database. A batch row is therefore exactly the row a press makes: its own activity row, the presser's session
  held per run, and the same queue.
- A run set in `work_list_runs`, so the work lists' existing `GET /api/work-lists/runs/sets/{set_id}` shows the
  batch's progress.
- A schedule on a work list is a `resource_schedules` row with `entity_type = "work_list"`, created through the
  ordinary `POST /api/schedules/work_list/{slug}` (DatabaseQualitySurvey only, `target_kind` "survey", database
  lists only). `scheduler._run_due` and "run now" dispatch it.
- On the bench of a database work list: "quality checks across this list…" opens `stages/quality-batch.js`. It shows
  the priced rows, "Run on N", the outcome for each member, and the schedule control with the skip rule stated
  before anyone schedules.

**No DDL.** Nothing new is stored: the schedule reuses `resource_schedules`, the set reuses `work_list_runs`, and
everything the price and the skip rule read is already stored.

## Questions and answers

| # | Question | Answer | Source |
|---|---|---|---|
| 1 | Is the batch one run or many? | Many: one run per database, each with its own row and price. Every result, verdict and proof row stays per database | §2.2 point 2 (design session for the owner, 2026-10-21) |
| 2 | Which path does it go through? | The same queue and the same credential gate as a single press (the work-list batch run, pointed at the definition) | §2.2 point 2 |
| 3 | What does the price show? | For each database, before the batch starts: tables, and bytes from the catalog ("size unknown · never analysed" where it is). The time is the measured wall time of that database's earlier runs, so each run's wall time becomes the next estimate | §2.3; §2.2 ("per-run price shown before the batch starts") |
| 4 | Is a large table capped or refused? | No size cap and no refusal. The per-table budget is 5 minutes, stated on the price | §2.3, §4 (owner, 2026-10-21: "5 minutes per table") |
| 5 | Who does a scheduled batch run as? | The schedule's creator, through the scheduler's existing `Daemon(SCHEDULER, requested_by=<creator>)`. No presser session is held, and members are checked as the creator at each firing | §2.5 (service account only for unattended batches); Brief Z2 round 3 (timer runs as the schedule's creator) |
| 6 | What is the "inventory", and what counts as "did not change"? | The inventory is RE's stored survey: its tables, its columns with their types, and each table's row count. The latest inventory is compared with the one the last quality run followed (the newest `database_value_profiles` batch). "Could not tell" (no quality run, no inventory, a row count not measured on either side) runs, never skips | **assumed**: §2.2 says "the existing change-detection rule" without defining it for a database. This follows `schema_diff` / `db_change_rates` (two stored snapshots, absence ≠ unchanged) |
| 7 | Does a press skip unchanged databases too? | No. A person pressed, so every runnable database runs, and its row only says whether it changed. The skip applies to scheduled batches | **assumed**: §2.2 attaches the skip rule to the schedule |
| 8 | Is the schedule on the list or on each member? | On the list. It follows membership, so each firing runs over the members the list has at that moment | §2.2 point 3 ("Automate schedules the batch (the work list and the definition)"); REPLY-DESIGNER-GROUP-SCHEDULING-AND-FIRST-INDEX-2026-10-19.md §2 (a Group schedule follows membership). **Applying the Group rule to a work list is assumed** |
| 9 | Do a scheduled list's members run together or separately? | Separately, at the same time: one queued run each | §2.2 point 2; the designer reply's "left open 2" is answered by it |
| 10 | What if a member also has its own schedule for the definition? | The member's own schedule wins. The list's firing leaves that member alone and reports "own schedule · runs <cadence> on its own" | Designer reply §3 ("the repository's own schedule wins") |
| 11 | Which definition can a work list be scheduled for? | DatabaseQualitySurvey only, on database lists only. Anything else gets a 422 with its words | **assumed**: Q3's scope; other definitions on a list are a later slice |
| 12 | Is the creator's publish right checked when a list schedule is saved? | No. It is checked per member at each firing, because the members are whoever is on the list then. A refused member is named in the run's words, and the rest run | **assumed**: follows Brief Z3's per-member rule for batches |

## Not in this slice

- **A failed scheduled run reaching a person.** The designer reply §5 asks for a sidebar cue and one RFA per failing
  schedule. Today a work-list schedule's failure is on its Schedules row and its activity row, the same as every
  other schedule.
- **Schedules table rows that expand to their members** (designer reply §2). The list's row shows the batch summary in
  its activity entry.
- **Mixed-kind lists** (work lists §5). They need the member-level `entity_type` DDL, which is the work-lists slice's
  own change. Q3 refuses any list that is not a list of databases.
- **Volume.** At batch scale, Q5's R5 (one `GovernedBy` edge per check per run) applies once Q5 publishes rules.
