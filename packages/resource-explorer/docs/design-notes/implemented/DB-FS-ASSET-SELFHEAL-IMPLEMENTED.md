# Database/filesystem asset self-heal — implemented (2026-10-03)

Environment proof: `resource_explorer.__file__` =
`/Users/dwolfson/localGit/egeria-v6/trellis-re-dbfs-selfheal/packages/resource-explorer/resource_explorer/__init__.py` (worktree `trellis-re-dbfs-selfheal`, branch `re/db-fs-asset-selfheal`).

## Defect
After an Egeria reset, two databases kept `egeria_asset_guid` values Egeria no longer held and
still read `is_published: True`. The scheduled `egeria_resync` scan read only the `projects`
table; only the CLI-only `recheck_all_linkages` flagged them. Backlog: "No self-heal for a dead
database/filesystem Egeria GUID".

## What changed
- `egeria_resync.py`: new scans `_scan_dbfs_assets` (every database/filesystem with a cached GUID,
  re-verified each pass via `_resolves`; True/False/None, None is undetermined and never gone) and
  `_scan_flagged_dbfs_rows` (existing stale rows, so the heal stays reachable when the dead-GUID
  finding is empty). New repair step `flag_stale_dbfs_assets` in `REPAIR_STEPS` and
  `SAFE_SCHEDULED_STEPS` (comment updated). It flags stale in `egeria_linkage_status`, clears the
  flag when the GUID resolves, skips rows with no GUID, deletes no GUID, writes nothing to Egeria.
- `egeria_linkage.py`: new shared `record_linkage_verdict` (the flag/clear write half);
  `recheck_all_linkages` now uses it, so the CLI sweep and the scheduled pass cannot drift.
- `web/static/next/admin/resync.js`: blast-radius sentence for the new step.
- `tests/test_egeria_resync_dbfs_selfheal.py`: 10 tests (stub client, temp SQLite).

## Decision
`recheck_all_linkages` is not what the step calls: it builds its own MetadataExpert client (not
injectable), raises a summary RFA, and sweeps repos too, whose heal is `clear_stale_assets`. The
step reuses resync's injectable client and `_resolves`, and shares only the write helper.

## Not done / not verified
- No outbox enqueue kind for database/filesystem asset publishes (out of scope).
- No live verification against Egeria; the type-agnostic element read
  (`ClassificationExplorer.get_element_by_guid`) is unproven for database/filesystem assets live.
- Whole suite not run.
