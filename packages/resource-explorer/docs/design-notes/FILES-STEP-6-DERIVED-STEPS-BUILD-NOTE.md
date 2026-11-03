# Files build step 6: the derived steps and the price line (build note, 2026-10-28)

Branch `claude/re-files-steps-6-8`, cut from `origin/main` at `865f98a8`. Brief:
`BRIEF-FILES-QUESTIONS-AND-ANALYSES-TO-RETIRE-CLASSIC.md` (the brief below). Nothing here writes to Egeria or to the
shared registry. The new tests run on scratch SQLite files. The one full Resource Explorer suite run on this branch did
not: its Postgres-backed fixtures opened a per-process test schema on the shared Postgres (:5442), so the `accessed_at`
migration may have run inside that test schema (not checked). Local suite runs should set `PGVECTOR_PORT=1` so those
fixtures skip.

## 1. What "steps 6–8" are, mapped onto the brief

From `origin/main`'s history, the Files build steps so far:

| Step | Brief | On main |
|---|---|---|
| 1 | §3 item 0: register on /next, attach the root folder | #798 |
| 2 | §3 item 0: the reach fork and the folder survey | #808 |
| 3–4 | §3 item 1: walk extensions (per-file rows, owner/mode, hash, folder bulk) and `folder_narratives` | #826 |
| 5 | §3 item 2: `file_profiles` | #850 (passed live 2026-10-26) |
| **6** | **§3 item 3: `fs_change_rates`, `fs_duplicates`, `fs_activity`, `fs_permissions`, plus F12's price line** | **this branch** |
| 7 | §3 item 4: `fs_layout_signals` and the narrative docs ingestion (F10, F11's long form) | not built |
| 8 | §3 item 5: `data_class_match` over file profiles (F7) | not built |

Checked on `origin/main` before building: none of the four step names existed as code (only in docs and question
gaps); `change_rates` in `examples/steps/` is the database step (Q-0086), not `fs_change_rates`. The walk's per-file
rows (`filesystem_walk_files`) already carried size, mtime, owner, group, mode, world_readable, content_hash and
hash_state, but **not the access time**, which `fs_activity` needs (one column added, §4). Analytic Library steps
attached to databases only.

**This branch stops after step 6** (item 3 plus F12). Steps 7 and 8 are separate slices: step 6 alone touches the
library, the ODS reader, the host, the run queue, a route, the card, two catalogs and the registry, which is already a
full review.

## 2. What was built

- **Four Analytic Library steps**, each its own installable package under
  `packages/trellis-analytics/examples/steps/` with a `StepManifest`, a full declaration (result kind, measures with unit
  and as-of, the four absence sentences, an extent, confidence `measured`, scope `resource`) and its nothing-to-derive
  guard:

  | Step | Question | Measures (annotation type) | Guard words |
  |---|---|---|---|
  | `fs_change_rates` (F5) | Q-0102 | files added/removed/modified, growth (ResourceMeasure) | "one walk only; a change needs two"; a capped walk leaves added, removed and growth "not measured" |
  | `fs_duplicates` (F6) | Q-0106 | duplicates here; content also held by another registered file system (ResourceMeasure ×2) | "no hashes", naming why from the walk's hash states |
  | `fs_activity` (F8) | Q-0107 | hot/warm/cold by modification; by access (ResourceProfile ×2) | "access times not recorded on this walk" / "… on this mount" |
  | `fs_permissions` (F9) | Q-0108 | owners, groups, modes as counts (ResourceProfile); world-readable secret-named files (RequestForAction) | "no stat fields"; "file names not classified on this walk" |

  Every result is for the whole file system and **names no file, folder, owner or hash**: a result can be published,
  and the counts-only rule of the folder publish (Files step 2 fix round 1, `folder_survey_annotations.py`) applies.
  Absence is never zero: every guard is `not_established` with its reason; `nothing_found` is only a measured nothing.

- **Typed reads** on `OdsReader` (and admitted to `STEP_ODS_METHODS`): `filesystem_walk_runs`, `filesystem_walk_files`,
  `filesystem_hash_matches`. Parameterised SELECTs RE owns; a step still sends no SQL.
- **File systems take library steps**: `survey_step_host.attach("filesystem", …)` from the filesystem adapter, `refresh`
  for both types, the generator writes a `filesystem_analyses` block, readers for ResourceProfile and RequestForAction.
- **The run path**: `POST /api/filesystems/{slug}/analyses/{analysis_id}/run` (validates, then queues the new run kind
  `filesystem_analysis_run`; closes the per-analysis 404 noted at F-05 for these steps), `workflows.analysis.
  run_filesystem_analysis` through the same executor a survey uses (cost observed, `step_runs` row, results in
  `step_annotations`, stored and not published).
- **The card** (`fs-survey.js`): the price line before the Survey press, "Derived from the walk" with each step's stored
  state and words, one Derive press that queues and polls each installed step.
- **F12, the price line** (`walk_derived.price`): the last walk's files and bytes, its wall time on this machine (the
  walk now records `walk_seconds` and `machine` in its limits), capped or not ("the full tree is larger and its size is
  not known"), the directories not walked, and the heaviest top folders' share of the bytes (the "not worth it" half).
  "not priced" until a walk has measured the tree; an Egeria-led file system says its time is Egeria's.
- **Questions**: Q-0102 now answered by `fs_change_rates`; Q-0106 (F6), Q-0107 (F8), Q-0108 (F9) and Q-0109 (F12)
  appended to `resource_questions.csv`; `question_catalog.yaml` and the Dr.Egeria questions document regenerated (the
  document is not run in Egeria).

Fixed in passing, on the same card: a successful re-read after a failed press cleared the press's error before it was
painted, so a failed Survey, Publish or Profile press showed nothing. The re-read no longer clears it.

## 3. Open points and their answers

| Open point | Answer | Source |
|---|---|---|
| Hash limit (brief §6) | 10 MiB per file (SHA-256), with a 2 GiB / 5 min hashing budget per run, env-configurable, as step 3–4 built it; unchanged here | `walk_extensions.py` (#826). No owner pick recorded: **assumed, owner to confirm** |
| Cross-file-system duplicates in this slice? (brief §6) | Yes: `fs_duplicates_elsewhere`, against each other registered file system's newest walk | The brief's own F6 row ("cross-resource by hash"). **Assumed, owner to confirm** |
| Where the CSV lister lives (brief §6) | Not touched | Out of this slice |
| Hot and cold thresholds (F8) | Hot: modified or read within 7 days; cold: over 365 days. Manifest `parameters`, read by the code | None recorded. **Assumed, owner to confirm** |
| "Sensitive" for F9 before F7 exists | The walk's own secret-name rule (the names it refuses to hash); data-class matches over file profiles join in step 8 | Brief F9 says "joined to F7"; F7 is not built. **Assumed, owner to confirm** |
| Telling "access times off on this mount" from "nobody read anything" | Cannot be told apart: when no file's access time is later than its modification time, the access measure is "not measured · access times not recorded on this mount" (never cold) | Brief F8 ("can't tell when atime is off"); the test is **assumed, owner to confirm** |
| Resource Explorer's own reads skewing access times | The access time is taken before the walk hashes; a file the previous walk hashed and last read after that walk began counts as "possibly Resource Explorer's own read", never hot | Builder's choice to keep F8 honest. **Assumed, owner to confirm** |
| Publishing to Egeria | Types declared and emitted (ResourceMeasure, ResourceProfile, RequestForAction for the world-readable sensitive count); **the write is not built**: RE's engine host is database-only and publishes ResourceMeasure and Classification only, and the walk's own publish is not extended. Results are stored in RE and said "kept here · not published to Egeria". When built, they go in RE's own file-system survey report (decision below) | Brief §3 item 3 asks for it; **follow-up** |
| What may be in a published result | Counts only; no file, folder, owner or group name and no hash | Step 2 fix round 1 (`folder_survey_annotations.folder_specs`, "coordinator ruling pending the owner's word") |
| Run inline or queued | Queued, through the run queue (catalog `availability: queued`) | `survey_step_host.catalog_entry`: "a step never runs in the web process (brief 3a)" |
| Nothing-to-derive guard | `survey-completed` for all four: an absence honestly recorded is a completed survey | Design ruling 2026-10-15 for `change_rates` (one snapshot), applied by analogy. **Assumed, owner to confirm** |
| Stages | `fs_change_rates` discovery; the other three analysis | Brief §2 table |
| Appending F6, F8, F9, F12 to the question catalog | Q-0106–Q-0109 | Brief §2 ("the catalog assigns ids on the append"); the owner's 2026-10-20 picks name them as what follows the first slice. **Assumed, owner to confirm** |
| "Which parts are not worth it" (F12) | The directories the walk did not enter, and the heaviest top folders' share of the bytes; leaving those out is the saving | Brief F12. **Assumed, owner to confirm** |

## 4. DDL

One additive nullable column, written in code only, never applied to the shared registry:
`filesystem_walk_files.accessed_at TEXT` (`docs/ddl-walk-files-accessed-at.md`). Old rows read NULL, "access time not
recorded on this walk". No other DDL: the walk timing is in `survey_data`, the run kind is a Python tuple entry
(`runs.kind` has no CHECK), results go in `step_annotations`.

## 5. Follow-ups

**Decision (project owner, 2026-10-28):** measures chosen in the publish table for a file system publish through a
file-system survey report of Resource Explorer's own, not the root-folder publish (the owner's answer to publish-table
question 3, relayed by the Monday Coordinator). The derived steps' annotations ride that report, the way a database's
ride RE's survey report.

1. The Egeria write for file-system library steps: their annotations in RE's own file-system survey report (the
   decision above), with ResourceProfile and RequestForAction admitted to the publishable types. The same report
   carries the publish table's chosen measures, which need three things from it (ext-publish-table, 2026-10-28,
   mirroring the database path `publish_local_survey(declared_measures=True)` and
   `publish_table.annotations_for` / `record_proofs`; their note `DECLARED-EXTENSIBILITY-SLICE1-PR2-PUBLISH-TABLE.md`
   §4a):
   - a hook that appends extra annotations AFTER the report's own, so the report's indices and qualified names do not
     move (`publish_table.annotation_for(row)`, check_name `declared_measure.<analysis>.<measure>`);
   - a read-back of the report by qualified name, giving each appended annotation's GUID or "" (`read_back_guids`,
     `{index: guid}`);
   - the report GUID, so proof rows (`catalogue_commit_proofs`, node_kind `measure`) are keyed by the file system's
     slug.
   Only the press that records proofs opts in (`declared_measures=False` by default).
2. Steps 7 (`fs_layout_signals`, docs ingestion) and 8 (`data_class_match` over file profiles; then F9's "sensitive"
   joins it).
3. The per-file lists behind the counts (which files are duplicates, which are world-readable and secret-named) on the
   Contents view: RE's own screen only, read from the walk rows.
4. The DDL test against a private Postgres (not run here).
