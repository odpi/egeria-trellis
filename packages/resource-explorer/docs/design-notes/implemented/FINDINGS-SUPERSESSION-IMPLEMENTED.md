# Findings supersession for telemetry, provenance and SLA — implemented (2026-10-03)

Written by the coordinator from the builder agent's report (the agent was not allowed to write the file).
Branch `re/findings-supersession`, tip `2103ea95` before this note.

## Environment proof (before any test run)

`uv run python -c "import resource_explorer; print(resource_explorer.__file__)"` printed
`/Users/dwolfson/localGit/egeria-v6/trellis-re-findings-supersession/packages/resource-explorer/resource_explorer/__init__.py`.
Tests ran with `PGVECTOR_PORT=1` and a temp SQLite registry; nothing touched the shared Postgres or any other service.

## What was wrong

Supersession is opt-in per writer, through `upsert_finding(..., supersedes_previous=True)`. Only the security family had
opted in. `telemetry_scan_findings`, `contribution_provenance_findings` and `sla_content_findings` used the default, so
older runs stayed `superseded_at IS NULL`. On 2026-10-02 that was 64, 91 and 50 non-superseded rows registry-wide
(`secret_scan_findings` had none stale).

`query_findings` already reads only the newest `surveyed_at` per (slug, kind, scope) and skips superseded rows, so
`context_compile`, `members`, `gaps` and the question facts were already correct on unrepaired data. The inflated
numbers came from bare SQL counts and from one unguarded reader, `ProjectRegistry.analysis_result_summary`, which did a
`COUNT(*)` over every run, superseded or not. The states matrix behind the Survey & analyses and By analysis panes uses it.
No other SQL that counts findings for display was found.

## Changes

**Write path.** `_persist` in `telemetry_scan.py`, `contribution_provenance.py` and `sla_content.py` now passes
`supersedes_previous=True`, as `secret_scan.py` does. `upsert_finding` itself is unchanged: it already stamps older rows
for the exact (slug, kind, scope) with the new run's `surveyed_at` as `superseded_at`, in the same connection and
transaction as the insert, also when the new rows are empty (a measured empty). A run that raises never reaches
`_persist`, so it writes nothing and retires nothing. Scoped writes supersede only within their own scope. No migration.

Inherited from secret_scan and kept: an early "unverified" outcome (empty inventory, no source files) is persisted as a
`scan_summary` row and DOES supersede the previous run. The owner should know.

**Reader.** `analysis_result_summary` now counts only non-superseded rows at the newest run per (slug, kind, scope).
`measured_at` is still the newest timestamp over all rows, so a measured-empty run reads `rows == 0` with its date. A new
constant `ALL_RUNS_FINDING_KINDS = (architecture_recovery, architecture_decisions)` gets only the superseded filter,
because those are read across runs by design. The metrics table has no `superseded_at` and is unchanged.

**Repair script `scripts/repair_findings_supersession.py`.** Targets only the three kinds. Dry run is the default and
prints per resource and kind the number of older non-superseded rows, the number of older runs and the run id kept:
counts and ids only, never finding text; the scope is printed as a length. `--apply` marks those rows in one transaction
per resource; a second apply changes nothing. Refusals: any non-SQLite URL (treated as the shared registry) without
`--i-know-this-is-the-shared-registry`; no registry named (it never falls back to the configured one); a missing SQLite
file (never created). It does not run the registry migrations and stops if `superseded_at` is absent. It was NOT run
against anything real. Applying it to the shared registry is the owner's decision, after a peer check.

## Audit of every findings kind written by a step

| Kind | Before | After |
|---|---|---|
| secret_scan_findings, cve_scan, security_hygiene, security_summary, security_features | supersedes | supersedes |
| **telemetry_scan_findings, contribution_provenance_findings, sla_content_findings** | **no** | **yes** |
| cii_badge, foss_scorecard, license_classification, community_support, chaoss_metrics, maturity, documentation, repo_classification, repo_sub_resource_survey, interface_surface, egeria_interfaces, deployment_evidence, dependency_support, refresh_plan, architecture_summary | no | no (single `_persist` call per run: convertible with one line each; not converted because each conversion changes what a clean run shows) |
| distribution, ci_quality, supply_chain, repo_conventions | no | no (two writers each: `manifest_parse.py` and `ingestion/pipeline.py`) |
| architecture_recovery, architecture_decisions, architecture_blueprints, architecture_diagram, architecture_interfaces, architecture_doc_lens | no | no, deliberately (multi-writer or multi-call per scope) |
| Egeria-materialised kinds (dynamic) | no | no (appended per report, deduplicated by a report marker) |

Their display readers already use `query_findings` (latest run wins). Opting the 15 single-call kinds in is a decision for
the owner.

**architecture_recovery** (the 1262 rows with NULL `superseded_at`) is NOT the identical one-line case and was not fixed.
It is written by two independent steps (`repo_arch_detect`, `repo_arch_coupling`), makes several `upsert_finding` calls per
scope (a component row, a structural-node row, one row per evidence item) and is read across runs by
`query_findings_all_runs`. Setting `supersedes_previous=True` on one call would retire sibling calls' still-valid rows.
The real fix needs run-keyed supersession using the `run_label` that `persist.py` already carries for withdrawals.

## Tests

New file `tests/test_findings_supersession.py`, 25 tests; every registry is a temp SQLite file with an explicit URL and the
fixture asserts a sqlite path under `tmp_path`. They cover: two runs of each of the three analyses leave only the second
current; a failed second run leaves the first current; a measured-empty second run supersedes the first; two resources and
two scopes do not interact; secret_scan is unchanged; an unconverted kind (cii_badge) still appends; the summary reader
counts only latest-run non-superseded rows on unrepaired fixtures, per scope, and still counts across runs for the
all-runs kinds; the repair script's dry-run default is counts-only, its apply is idempotent, and it refuses a server URL,
no registry, or a missing file (the override flag was proved with a stubbed `create_engine`, not a connection).

Red: with only `resource_explorer/` stashed, 12 of 25 fail (the supersede, measured-empty and other-resource cases for
all three analyses, plus the three summary-reader count tests). Green: 1097 passed across the explicit file list (the new
file, `test_registry*.py`, `test_context_compile*.py`, `test_stats_fetcher.py`,
`test_by_analysis_headline_matches_questions.py`, the telemetry, SLA, provenance and secret-scan tests,
`test_arch_recovery_finding57.py`, `test_annotation_finding_derivation.py`,
`test_repo_classification_finding_labels.py` and all 46 `tests/test_next_*.py`), run with
`-m "not requires_egeria and not live_egeria_writes"`.

## Merge-tree

Against `origin/re/db-results-readers`: conflicts only in `tests/test_db_fs_results_and_questions.py`, a file this change
does not touch (the conflict is between that branch and `main`). `registry.py` merges cleanly.

## Not verified

- Nothing touched the shared Postgres on 5442 or any other service. The real counts (64 / 91 / 50) were not reproduced and
  the repair's real dry-run output is unknown.
- The Postgres dialect of the new `analysis_result_summary` SQL and the script's `information_schema` branch are checked
  by reading only; tests are SQLite.
- Whether any frontend screen shows the `rows` number beyond `has_results`.
- `superseded_at` stores the superseding run's timestamp, not a run id, so two runs with the same `surveyed_at` would be
  indistinguishable.
