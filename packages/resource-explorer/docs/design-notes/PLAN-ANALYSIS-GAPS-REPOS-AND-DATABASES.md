# PLAN — closing the repository and database analysis gaps (2026-10-02)

From the coordinator, at the owner's request ("schedule the repo and db analysis gaps",
"don't forget to complete work on repos and DBs"). Source: the read-only inventory of
2026-10-02 against main `aa7169da` (declared catalog, step registry, question catalog,
the 2026-09-11 coverage audit and the implemented notes). **It is documentary: the
registry was not queried, so every "ran" verdict comes from the audit or a note.**
File systems come after databases (owner, 2026-10-01).

## What the gaps actually are

`availability: queued` in `analysis_catalog.yaml` is an execution mode, not "unbuilt".
Real gaps, by kind:

| kind | declared | built and run | built, no run evidence | declared, not built | designed, not declared |
|---|---|---|---|---|---|
| repository | 39 | 31 | 8 | 0 | 5 |
| database | 25 | 20 | 2 | 3 | 8 |

The four repo analyses the owner sees as missing (`secret_scan`, `telemetry_scan`,
`contribution_provenance`, `sla_content`) are built, but sit only in the Compliance and Full
survey definitions, not Assessment; on 2026-09-11 they had run on 1 to 2 of 14 kept repos.
Nothing needs building for them: it needs a decision and a run.

## Schedule

Waves are ordered by dependency and cost. A slice is "done" when merged, with a behaviour
test, and an owner gate where the screen changes.

### Wave 1: databases, no design needed (agents dispatched 2026-10-02)

| # | slice | why first | gate |
|---|---|---|---|
| D1 | Results readers for `data_class_match`, `reference_data_match`, `nested_column_profile` (a local detail table and entries in `DATABASE_ANALYSIS_RESULTS_MAP`) | built and ran; the UI says "no summary reader yet"; blocks the governed-share comparison | coco_pharma shows a result for each, or an honest "not run" |
| D2 | `db_hub_tables` as a seventh `db_derived` check | Backlog says almost free; closes one question gap | the question answers on adventureworks |
| D3 | The "Who owns this resource" question text: stop showing repo wording (`repository_health`, `chaoss_metrics`) on a database; the catalog row says gap but the 09-23 ruling says mixed/human | owner saw it on 2026-10-02 | the Context pane of a database reads correctly |

### Wave 2: repositories, needs two owner decisions

| # | slice | decision or dependency |
|---|---|---|
| R1 | Run the Compliance survey on the repos kept for investigation | **Owner:** run it? It writes survey rows to the shared registry and calls GitHub; needs a peer check |
| R2 | Should Assessment include `secret_scan`, `telemetry_scan`, `contribution_provenance`, `sla_content`? | **Owner decision.** Otherwise the gap recurs for every repo reached from the 44 unanalysed |
| R3 | Record a run for the 8 built-not-known-to-run analyses, or retire `repo_profile_refresh` | after R1 |
| R4 | Integrations derivation, then upgrade-process read (RAG over CHANGELOG), then AI/ML licensing | all derive from data already collected; similar repos last (needs design first) |

### Wave 3: databases, small reads and the owner's wishes

| # | slice | note |
|---|---|---|
| D4 | Multi-snapshot series in `db_change_rates` (growth over time beyond two snapshots) | owner wish; `table_growth` and its chart already exist |
| D5 | Table-level change history (removed columns and schemas by name; any run pair) | owner wish; the comparators are built |
| D6 | `db_documentation_coverage`, then `db_server_profile` | `db_server_profile` needs a `target_shape` decision first |
| D7 | `column_profile` as a declared analysis, then the modelling-score combiner | consume step outputs that already exist |

### Wave 4: last

`semantic_suggestions`, doc-source ingestion (needs LLM or ingestion work). Scope slices 20 to 22 of
`COORDINATOR-BRIEF-MULTI-RESOURCE.md` interact (scoped analyses key results by scope): do D1 and D7 before or
alongside slice 20 to avoid rework.

### File systems (after databases)

Run `filesystem_inventory` on a real folder; extend `grain_determination`, `data_class_match`,
`reference_data_match` to `filesystem`; then `file_kind_breakdown`, `path_conventions`,
`fs_change_rates`. **Owner distribution** is in no plan: it needs a design note first (what "owner" means on each OS and
whether the walk already stores `st_uid`).

## Rules for every slice

Each slice follows the repo's standing rules: a behaviour test red on main then green; every status word comes from
a persisted row; "not measured" is never drawn as zero; its own worktree; signed commits; a peer check before any
shared-registry migration; no reading back from Egeria until the source-of-truth rule is decided.

## Not verified

Whether the 8 built-not-known-to-run analyses and `db_external_dependencies` produce output live; the kept-repo list
since 2026-09-11; the live Egeria survey definitions against the committed ones. This note replaces nothing: the
coverage audit and the inventory remain the evidence.
