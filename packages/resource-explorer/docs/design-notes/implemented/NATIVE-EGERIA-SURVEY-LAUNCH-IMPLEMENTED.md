# Native Egeria survey launch — implemented (2026-09-30)

**The brief and the owner's six-point gate are in
[`BRIEF-NATIVE-EGERIA-SURVEY-LAUNCH.md`](BRIEF-NATIVE-EGERIA-SURVEY-LAUNCH.md)**
(design session, 2026-09-30) — that document is the authoritative source and is
not restated here. This one records what was built, what was found live, the
calls that were made where the brief was silent, and what is verified.

## What was built

On Survey & analyses, for a database or filesystem, the "Also known to Egeria"
list (informational since #244) is now **Egeria's own surveys**: one row per
native process in `configdata/technology_type_processes.yaml`, each with

- **Run** when RE can run it (`kind: survey_existing`, resource catalogued in
  Egeria), submitting an engine action through pyegeria
  (`AutomatedCuration.initiate_gov_action_type`, action target
  `serverToSurvey`, or `fileToSurvey` for the folder survey — now a per-process
  `action_target_name` in the YAML);
- **a reason instead of a Run** when it cannot: `catalog_and_survey` (needs a
  connection template RE does not collect) and a resource with no Egeria asset.
  These are drawn `◌` "can't be run from here — <why>", never `○ not run`.
  `delete` processes are not listed at all, as before.

| piece | where |
|---|---|
| proof columns + idempotent annotation store | `registry.py` (`step_runs` ADD-only columns, `native_survey_annotations`) |
| derivation, submit, read-back, sweep | `native_survey_run.py` |
| routes `GET/POST /api/native-surveys/{entity_type}/{slug}[/run\|/refresh\|/reports/{guid}]` | `web/routes/native_surveys.py` |
| read-back with no tab open | `scheduler._sweep_native_surveys` |
| rows, Run, poll, report view | `web/static/next/stages/native-surveys.js`, wired in `loadSurveyPane` |

## Every status word derives from a proof row

`derive_native_state(run, stored_annotation_count)` is the one pure function that
turns rows into words; `tests/test_native_survey_run.py::TestDeriveNativeState`
walks every combination, and three sabotage runs (drop the count check, drop the
never-blank message, drop the poll) each fail a specific test.

| word | proof |
|---|---|
| submitted to Egeria · time | `step_runs.engine_action_guid` |
| running · Egeria says X · read time | `engine_action_status` in Egeria's active set + `engine_action_read_at` |
| complete · read time · report time · N annotations | status COMPLETED **and** `survey_report_guid` **and** `report_read_at` **and** the count of annotations actually stored equals the count recorded when they were read |
| `<Egeria's word>` · message | any other terminal status, verbatim; an empty message becomes "Egeria gave no message with this status", never blank |
| not submitted — Egeria did not accept it | a row with `submit_error` and no GUID |
| submitted … last read failed | GUID stored, no successful read yet, `engine_action_read_error` set |

A failed *read* is recorded as a failed read (it keeps the last good status
beside it); it is never turned into a status. Zero annotations on a COMPLETED
report is a legitimate complete, not an absence.

## Found live (read-only, 2026-09-30) — and worth knowing

1. **An engine action is linked to its report by a `ReportOriginator`
   relationship.** The report is found from the action
   (`get_related_metadata_elements(action, "ReportOriginator")`) and cross-checked
   from the report's own `reportOriginator`. This replaces the timestamp
   attribution in `egeria_async_survey_result.py`, whose design note recorded "no
   direct EngineAction-to-report relationship was found." That note predates this
   and is wrong; the older path is untouched by this slice (a follow-up could move
   the whole-definition `executes_at: egeria` handler onto the relationship).
2. **A native survey is slow.** The last completed one took ~14 minutes for a
   157-table database. Submit therefore returns as soon as Egeria accepts; the
   read-back is separate and repeatable (UI poll every 8s while in flight, plus
   the scheduler sweep so it finishes with no tab open).
3. **`egeria_survey_reader.get_survey_reports_by_guid` / `get_annotations_by_report_guid`
   swallow every exception and return `[]`.** `[]` there cannot distinguish
   "no reports" from "the read failed" — exactly what happened for coco_pharma
   below. This slice does not use them; `annotations_from_report` was factored
   out (pure, same output) so the read-back can fetch the report itself and let
   failures raise.
4. **coco_pharma's stored `egeria_asset_guid` does not exist in Egeria**
   (`5246aa50-…`, "not found" on a direct read; the repository store was wiped on
   2026-09-04 and this database was never re-linked). Run is checked live
   (`asset_exists`) before submitting and says so — "Egeria has no asset with the
   GUID RE has stored… Publish the resource to Egeria again" — rather than
   submitting and failing later. **The gate on coco_pharma needs it re-catalogued
   first.**
5. **A live submission on adventureworks (2026-09-30 21:28, through the real
   port, no RE registry rows) was accepted and then FAILED on the Egeria side:**
   `FATAL: role "default" does not exist` (OMES-SURVEY-ACTION-0018,
   `HikariPool$PoolInitializationException`) — the Egeria asset's connection
   points at a Postgres role that is not there. That is a catalogue/connection
   problem on the Egeria asset, not RE's. It is, usefully, a real example of
   gate point 5 (Egeria's status word and full message are what the row shows).
   Gate point 3 (complete) on adventureworks will need that connection fixed; the
   complete path was instead verified read-only against the earlier COMPLETED
   action (below).

## Calls made where the brief was silent

- **Where "RE's findings" for a database live.** Databases have no
  `project_analysis_findings` (that table is FK'd to `projects`); their results
  are the `database_*` tables and `database_surveys`. A native report's
  annotations are Egeria's own vocabulary (`Capture Database Table Measurements`
  and so on) and do not map onto RE's schema shapes without guessing. So they
  are stored as-is in a new `native_survey_annotations` table and shown from the
  row ("N annotations ›" opens the report with its time). **This is the judgement
  call to review:** it does not put the annotations into the By analysis boards
  or the `database_*` tables.
- **Second run: dated beside, never duplicated.** A second run is a new engine
  action and a new report; its annotations are stored beside the first's, each
  set dated by its report's own time, and the row shows the newest run. Idempotent
  by `(report GUID, annotation GUID)` with `ON CONFLICT DO NOTHING`, so re-reading
  the same report (a retried poll, two browsers, the sweep) writes nothing. The
  stored count is read back from the table, not taken from the list passed in.
  This matches `database_surveys` (append, read the latest). Verified by the key
  that survives a merge (report + annotation GUID), not by names — see the
  dedup reference.
- **A second Run while one is in flight is refused (409).** Launching is not
  idempotent at Egeria; a second click would make a second engine action.
- **`step_runs.step_key` is `egeria-native:<process qualifiedName>`**, `executor`
  and `source` are `egeria`. The Prefect columns (`flow_run_id`, `dispatch_failed`)
  are never written by this path and the whole-definition Prefect run is
  untouched. One side effect: `step_run_metrics.cost_per_question(group_by='slug')`
  now counts these rows as runs with no measured vector (`partial`), which is
  honest.
- **Activity log:** one `triggered` entry per submission (CLAUDE.md rule 16), an
  `error` entry if Egeria refuses. The entry is not updated on completion; the
  row is where the outcome lives.
- **No outbox, no retries.** A launch cannot be create-blind-retried safely (see
  the uni/multi-link reference), and the submit is a single request whose GUID is
  the proof.
- **Identity.** The route reads the caller once, in the request, and passes
  `submitted_by` down; every Egeria call runs under `asyncio.to_thread`. The
  scheduler sweep takes no identity by construction (it reads Egeria and writes
  proof rows for runs someone else submitted), which is the safe shape for code
  that runs on nobody's behalf.
- **The real port keeps one event loop per instance.** Found the first time it
  made two calls: a per-call loop closed under pyegeria's cached HTTP client
  ("Event loop is closed"). Fixed and documented on `PyegeriaSurveyPort`; used
  only through `port_session`.
- **Filesystem** gets the same rows because it is the same code and the YAML
  already carries a live-verified `FileSurvey::survey-folder` with its
  `fileToSurvey` target — but it has not been run live.

## Verification

- `tests/test_native_survey_run.py` (50): the derivation table, submit, read-back,
  dedup, second run, sweep. `tests/test_native_surveys_routes.py` (10).
- **Render harness** `frontend-build/test-harness/native-egeria-surveys.test.mjs`
  (12): every state's row, the unrunnable row (◌, reason, no Run, no "not run"),
  the real pane via `loadSurveyPane`, Run → "submitted" → poll → complete, a
  refused Run, and a whole-pane re-render not wiping a status. The poll-removed
  sabotage fails the run→complete test.
- **Live, read-only:** `PyegeriaSurveyPort` against the real platform — asset
  exists/absent, action status and message, report via `ReportOriginator`, 638
  annotations read in 1.3s; a full refresh of the earlier COMPLETED action into a
  temp registry gave `complete`, report time, 638 stored, and a second pass wrote
  nothing.
- **Live, write:** one real submission (adventureworks) — proves the submit path
  and the failure display; see finding 5.

## Not done / open

- Findings are not yet surfaced anywhere but the row's report view (see the
  judgement call above).
- Owner gate points 2–5 in the browser on coco_pharma then adventureworks are the
  owner's; coco_pharma needs re-cataloguing and adventureworks' Egeria connection
  needs its role fixed before a run can complete.
- `egeria_async_survey_result.py`'s timestamp attribution could adopt the
  `ReportOriginator` link.

## Addendum (2026-09-30): credentials precondition

The 2026-09-29 redeploy reset the Egeria container's `/deployments/secrets/`,
losing the projected `resource-explorer.omsecrets`; surveys then failed minutes
after submission with `role "default" does not exist` (finding 5 above). Run now
checks first: when `EGERIA_SECRETS_STORE_LOCAL_PATH` is configured and the file
or this database's `<slug>::PostgreSQL Secret` collection is absent, the row is
not runnable and says "Egeria has no credentials for this database · re-project
secrets"; no submission is attempted. When the path is unset RE cannot tell: the row shows "? can't confirm Egeria
has credentials · secrets path not configured" and Run stays available (design
ruling). Exactly three states: present, absent (refuse), not-configured (warn). Tested against a nonexistent directory
(`TestCredentialsPrecondition`). Note this only protects a deployment that sets
that path.
