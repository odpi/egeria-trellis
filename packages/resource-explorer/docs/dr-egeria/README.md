# docs/dr-egeria — RE's Dr.Egeria documents

The bootstrap (`resource_explorer/bootstrap.py`) runs the **batches**. A batch is a sub-folder with a `_batch.json`,
named in `_folder_order.json`, in that order. A folder without a manifest, and every file at this level (the CSV
sources of truth, probe documents, this README), is never run.

| Order | Batch | What it declares | Idempotent | Healed automatically |
|---|---|---|---|---|
| 1 | `foundations` | glossaries, funnel stages, perspectives | yes | when its canary is missing |
| 2 | `questions` | the Question terms | yes | when its canary is missing |
| 3 | `survey-definitions` | the repository Survey Definitions | no (post_heal reconciler) | when its canary is missing |
| 4 | `survey-definitions-database` | RE's database Survey Definitions | no (post_heal reconciler) | when its canary is missing |
| 5 | `governance-metrics` | RE's GovernanceMetrics | yes | when its canary is missing |
| 6 | `engine-host` | RE's survey engine `ResourceExplorerSurvey`, its services, governance action types, the mixed process | no | **only when `RE_ENGINE_HOST_HEAL` is on** (default off) |

`engine-host` is generated, `_batch.json` included, by `scripts/generate_engine_host_declaration.py`. With the
setting off it is reported `opted_out`. RE still reads whether the engine is there, says so in the shell banner,
and an admin declares it with Admin › Repair › "Declare RE's survey engine in Egeria" (`POST /api/bootstrap/run
{"batch": "engine-host"}`). See `docs/admin-guide.md`, "Engine host".

**Not a batch, on purpose: `questions-catalog/`** (RS-W1-3, 2026-11-07). It holds `catalog-questions.md`, the Egeria
Overview's catalog-wide questions, generated from `resource_questions.csv` with `--resource-type catalog`. It has no
`_batch.json`, so the heal never runs it: its Perspective links have no reconciler, and publishing it is one
owner-approved run, once. Do not add a manifest. See `resource_questions_guide.md`, "Catalog questions".

One document, outside any heal: `resource-explorer dr-egeria run <path.md> [--dry-run] [--force]`.
