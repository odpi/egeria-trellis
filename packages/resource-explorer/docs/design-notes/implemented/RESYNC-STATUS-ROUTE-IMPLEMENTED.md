# Resync: status route + scheduled-flag deduplication — implemented

Closes two follow-ups a design-review session raised against the merged
Admin "Egeria Alignment" (Resync) pane
(`RECONCILE-ADMIN-IMPLEMENTED.md`,
`resource_explorer/web/static/next/admin/resync.js`). Both were things the
original implementer deliberately declined to build unasked — the pane's own
header comment named them as a known gap. This closes both.

## Item 1 — `egeria_resync.get_status()` now has an HTTP route

**Problem:** the Resync pane's "already scheduled" rows (backed by
`SAFE_SCHEDULED_STEPS`) reported only current drift state from a live scan.
There was no way to tell "the scheduler ran, checked, and correctly found
nothing to fix" from "the scheduler hasn't run in three days" — both looked
like an identical clean row.

**Built:** `GET /api/egeria/resync/scheduler-status`
(`resource_explorer/web/routes/egeria.py`), modeled directly on
`bootstrap.py`'s own `/status` route (`bootstrap_status()` ->
`bootstrap_mod.get_status()`) — same contract: async route, no Egeria call,
just returns `egeria_resync.get_status()`'s in-process state dict
(`last_run_at`, `last_reachable`, `last_unreachable_reason`, `last_applied`,
`last_error`, `consecutive_failures`).

**Naming note:** the obvious path, `/api/egeria/resync/status`, collides
with an existing route declared earlier in the same router —
`@router.get("/{slug}/status", ...)` at line ~292 of `egeria.py`. Starlette
matches path templates in declaration order, and `/{slug}/status` matches
`/resync/status` first (with `slug="resync"`), returning a 404 "Project
'resync' not found" instead of ever reaching the new handler. Caught by the
new route test actually hitting the route through `TestClient`, not just
unit-testing `get_status()` in isolation — exactly the "route -> function,
not just the function" coverage the task asked for. Renamed to
`/resync/scheduler-status` to avoid the collision (mirrors `resync/scan`,
`resync/apply`'s existing two-segment-literal pattern, which is why those
two never hit this).

**Frontend:** `getResyncStatus()` added to `re-api.js` alongside the
existing `getResyncScan`/`applyResyncSteps`. `resync.js`'s `load()` fetches
it in parallel with the scan (not awaited together, same pattern as the
private-zone fetch already there) so a status-fetch failure never hides the
scan. `scheduledRowHtml()` now takes the status object and, per the design
reviewer's exact requirement — "a consecutive-failure count earns a flag
when non-zero, silence otherwise" — renders a `⚠ N consecutive failure(s) —
last error: ...` line only when `consecutive_failures > 0`, plus
`last_run_at` unconditionally (when present) so a curator can judge recency
at a glance even on a clean row.

## Item 2 — removed the duplicate `SCHEDULED_STEPS` list in `resync.js`

**Problem:** `resync.js` kept its own hardcoded `SCHEDULED_STEPS` `Set`
mirroring `egeria_resync.py`'s `SAFE_SCHEDULED_STEPS` tuple — two sources of
truth for one fact, the pattern this codebase has been bitten by before.

**Fix:** `Finding.as_dict()` (`egeria_resync.py`) already had exactly this
shape of problem solved once, for a different duplication — the existing
`"expensive": self.repair_step in EXPENSIVE_STEPS` field, added specifically
so the frontend wouldn't keep its own copy of which repairs are slow. Added
a `"scheduled": self.repair_step in SAFE_SCHEDULED_STEPS` field right next
to it, same reasoning. `resync.js` now filters on `f.scheduled` /
`!f.scheduled` instead of a hardcoded `Set.has()` check; the `SCHEDULED_STEPS`
constant and its "mirrors the backend, must be updated by hand" comment are
gone. No new HTTP surface needed for this half — it rides on the scan
response the pane already fetches.

## What was NOT touched

Per the task's scope: `curate_commit.py`, `assess_freshness()`, and the
saved-search-as-discovery-source backlog item are untouched. No new Admin
surface was added beyond the status route and the two `resync.js` wiring
changes described above.

## Tests

- `tests/test_egeria_resync_status_route.py` (new): `Finding.as_dict()`'s
  `scheduled` field (present/absent, and disjoint from `expensive`), plus
  route-level `TestClient` tests for `/api/egeria/resync/scheduler-status`
  — including the regression guard that the route never touches
  `EgeriaResync`/pyegeria, only the plain status dict.
- `tests/test_next_admin_pane.py`'s `TestResyncPane` (updated): confirms the
  hardcoded `SCHEDULED_STEPS` constant is gone and `f.scheduled` drives the
  scheduled/repairable split; confirms the new `getResyncStatus` export and
  route string; confirms the consecutive-failures flag is conditionally
  rendered (`failures > 0`) and `last_run_at` is surfaced. One pre-existing
  test's body-slice window was widened (1400 -> 2200 chars) since
  `scheduledRowHtml` grew with the new status parameter and failure-flag
  markup.

Full suite: `uv run pytest tests/ -q` — 5197 passed, 103 skipped, 0 failed.
