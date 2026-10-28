# Resync apply proof and readiness - implemented

## Environment proof (before any test run)

resource_explorer imports from this worktree:
`/Users/dwolfson/localGit/egeria-v6/trellis-re-resync-apply-proof/packages/resource-explorer/resource_explorer/__init__.py`
Signed test commit verified `%G?` = G, then dropped.

Re-proved after `uv sync --all-packages --all-groups` in this worktree (own `.venv`),
printing the import path before every test run.

## What changed

1. `_publish_one` now wraps `_publish_one_unlogged` and writes an `activity_log`
   row (operation `publish`, status `error`, summary carries the reason) on every
   not-ok outcome. The 428 refusal returns before the orchestrator/publisher, so
   nothing else ever recorded it.
2. Both UIs keep a per-slug error list when `failed > 0` or `reachable === false`.
   Next (`next/admin/resync.js`): `applyFailures()` + `showApplyResult()`; on
   failure it returns without `load()` until "Dismiss and re-scan" is clicked.
   (Also resets `_busy`/button after a successful apply, which was left stuck.)
   Classic (`index.html` `_applyResync`): toast is `Resync applied: N ok · M failed`,
   red with a persistent list and dismiss button on failure, green only at 0 failed.
   Non-publishing steps count as one ok step each.
3. `_scan_registration_only` asks the same readiness question as the gate
   (`_ready_via`, now shared with `_publish_readiness`). Blocked repos move to a new
   `registration_blocked` finding (needs_decision, no repair step, plain sentence).
   The false "scan promised this would not happen" comment is corrected.
4. "Published" = a `project_egeria_surveys` row for the slug. A repo with a survey
   row and no live claims is done (scheduled-refresh path). Live claims that are a
   subset of `REGISTRATION_ONLY_ANALYSES` still mean "thin publish, offer the full
   survey", because a catalog-step publish also writes a survey row. No investigation
   is linked to a Project.

## Evidence

Tests in `tests/test_egeria_resync_apply_proof.py`. RED = the three source files
reverted to main, new tests and the helper change kept; GREEN = fixes restored.

| test | pre-fix | post-fix |
|---|---|---|
| refused publish leaves an activity_log row | FAIL (no write_activity call) | pass |
| both UIs persistent failure, no unconditional green | FAIL | pass |
| no-context repo: sentence, no tick-box | FAIL (`['docling','kafka'] == ['kafka']`) | pass |
| published = survey row | FAIL (`['docling','fresh'] == ['fresh']`) | pass |

Each test also carries a known-negative (success not logged as failed; inheriting
repo stays tickable; repo with no survey row stays listed). Suite: 144 passed,
1 skipped across the resync, scheduler, status-route, JS-syntax and render-mode tests.

## Not verified

- No live run against Egeria or the shared Postgres (forbidden). The UIs were pinned at
  source level (no Node in CI) and `node --check` passed on resync.js; neither was
  exercised in a browser.
- That docling/egeria_docs actually have survey rows live is the trace's finding,
  not re-measured here.
