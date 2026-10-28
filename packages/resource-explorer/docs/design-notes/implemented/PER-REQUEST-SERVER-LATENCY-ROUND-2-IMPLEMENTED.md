# Per-request server latency, round 2 — closing the two unmet gate items

**Status:** Implemented. Both round-2 targets (the survey_data jsonb-containment fix,
the `list_projects` N+1 fix) landed with real before/after numbers, a coordinated quiet
measurement window, and a full green test suite. Two of the three gate numbers now pass
comfortably; the third (board-summary <200ms) is closer but not fully met, and a related,
previously-invisible contributor (`list_databases`) was found and fixed along the way. See
"Gate results" below for exact numbers, and "What is still open" for what remains,
reported honestly rather than claimed.

This is round 2 of the investigation in `PER-REQUEST-SERVER-LATENCY-IMPLEMENTED.md`
(round 1) — read that first for the original symptom, the registry engine/schema caching
fix, and the `list_projects`-blocks-the-event-loop fix. This document only covers what
changed in round 2.

## What design asked for

Round 1 closed the general per-request overhead but left two gate items unmet:
board-summary requests on `laz_local_adventureworks` (57 accumulated survey rows, ~61MB of
`survey_data`) still cost ~700-900ms per real fetch, and the cold-load headline target
was still over 2s. Round 1's own report named the specific fix and flagged it as
correctness-sensitive rather than shipping it under time pressure: replace
`_credential_capability_results`'s "fetch every historical survey's full blob, scan in
Python" pattern with a server-side jsonb containment query — measured directly at the time
(~300ms vs ~900ms) but not implemented, because it touches the exact search path a real,
recent, live bug fix (`a0f28aec`, 2026-09-26) depends on getting right. Design's explicit
requirement for this round: land it WITH that fix's own regression test still green, AND a
second new test covering the exact search shape that bug involved.

Design also asked for the `list_projects` N+1 pattern (68 projects × 3 registry calls
each) to be batch-fetched instead, and added a fourth gate number: `/api/projects/` under
300ms with 68 projects.

## (a) The survey_data jsonb-containment fix

### Finding the bug it must not regress

`git log` around 2026-09-26 turned up `a0f28aec` ("Fix schema-count leading number and
credential-capability key mismatch"). Its second half is exactly the pattern round 1 found
expensive: `_credential_capability_results` used to read only the single latest survey row
(`get_latest_database_survey`), while the header's own `_to_summary` already searched every
stored survey newest-first — because a later, probe-less run must not silently hide an
earlier, still-valid capability reading. Found live on `coco_pharma`: the header showed
"sees 6 of 8 schema(s)" from an older run while `_credential_capability_results` saw
nothing, because the newer run hadn't re-run the probe. The fix made `_credential_
capability_results` search every stored survey too — the exact "search every stored
survey newest-first for one that has X" pattern round 1 identified as the ~900ms
bottleneck. `a0f28aec`'s own regression test,
`test_credential_totals_from_an_older_survey_still_show_when_the_latest_run_omits_the_probe`
(`tests/test_schema_inventory_headline.py`), is the one this round must keep green.

### The fix

New `ProjectRegistry.find_latest_database_survey_with_key(slug, key)` (`registry.py`):
returns the most recent stored survey whose `survey_data` JSON has a truthy top-level
`key`, or `None`. On Postgres, uses `jsonb_exists(survey_data::jsonb, %s)` as a `WHERE`
filter with `LIMIT 1`, pushing the newest-first search into Postgres instead of
transferring every historical blob to filter in Python.

**Deliberately not the `?` operator.** `PostgresCursorWrapper._translate_sql`
(`registry.py`) does a blind `sql.replace('?', '%s')` for this codebase's `?`-as-
bind-placeholder convention — that would corrupt a literal `?` containment operator in the
SQL text itself (turning it into `%s` and breaking both the query syntax and the parameter
count). `jsonb_exists(...)` is the operator's function form and uses no `?` character at
all, sidestepping the collision entirely rather than working around it.

**Correctness, not just speed.** Every write to `database_surveys` goes through
`record_database_survey`'s `json.dumps`, so the `::jsonb` cast is safe by construction
(verified: `grep -n "INSERT INTO database_surveys" resource_explorer/registry.py` finds
exactly one write path). Even so, the method can never be *less* correct than the linear
scan it replaces:
- The fast path's single candidate is verified truthy in Python before being trusted — a
  row that has the key but a falsy value (e.g. `{}`, not expected in real surveyor output
  but not assumed against) falls through to the exhaustive scan rather than being accepted.
- A query error (narrowed to `psycopg2.Error` / `sqlalchemy.exc.SQLAlchemyError` — see
  "The ratchet caught a real issue" below) falls through to the same scan.
- SQLite (no native jsonb) and any registry stub that only implements `get_database_surveys`
  (test doubles) both go straight to the same fallback, unchanged.

`_credential_capability_results` (`survey_definition_adapter.py`) now prefers this method
via `getattr`, falling back to the pre-round-2 linear scan for any registry that doesn't
implement it — the exact shape `tests/test_schema_inventory_headline.py`'s `_FakeRegistry`/
`_MultiSurveyRegistry` test doubles are, which is also why the 2026-09-26 regression test
needed no changes at all to keep passing.

`databases.py`'s `_to_summary` (the header's own credential-capability display) was
updated the same way, and separately its `latest` survey lookup (schema/table/column
counts) was switched from `get_database_surveys(...)[0]` to round 1's `get_latest_
database_survey` — removing the unbounded fetch from this route entirely rather than just
adding a second query alongside it (see "A regression I introduced and fixed within this
same round" below for how this was found).

### A second, self-contained cache — deliberately not touching the one PR/CI was fixing in parallel

Profiling showed `find_latest_database_survey_with_key` called up to 5 times within one
board read (`_credential_capability_results` has that many distinct call sites). Round 1's
`get_database_surveys` cache doesn't cover a different method, so this method got its own,
added **without editing `__init__` or `record_database_survey` at all** — a lazily-created
dict via `self.__dict__.setdefault("_survey_with_key_cache", {})` inside the method itself.

This was a live constraint, not a style preference: PR/CI dispatched a separate agent
mid-round-2 to fix a real staleness bug in round 1's `get_database_surveys` cache (three
long-lived `ProjectRegistry` holders — `run_queue.py`'s worker loop and reconciler,
`egeria_resync.py` — never invalidate it across runs), working on the same branch/PR in
parallel. Editing `__init__`/`record_database_survey` risked colliding with that fix
textually or, worse, semantically (inheriting whatever staleness problem it was solving).
Confirmed this new cache carries none of that risk on its own terms, not just by avoidance:
`grep` finds neither `run_queue.py` nor `egeria_resync.py` calling this method, `_credential_
capability_results`, or any of its board-reader callers — every real caller constructs a
fresh `ProjectRegistry()` per request, so this cache's worst case is "possibly stale for
the remainder of one HTTP request that both reads and writes the same database's surveys,"
not the cross-process staleness the other fix addresses.

### The ratchet caught a real issue

The first draft used a bare `except Exception` around the fast-path query, to fall back to
the linear scan on any failure. `tests/test_no_silent_success.py`'s baseline ratchet
correctly flagged this as a new broad-except/log-only site (113 vs. a 112 baseline).
Narrowed to `(psycopg2.Error, sqlalchemy.exc.SQLAlchemyError)` — the two layers that can
legitimately raise for "the database operation failed" (a malformed cast, a connection-
acquisition failure during pool checkout) — so a real bug in surrounding Python code still
raises and is visible, rather than being silently absorbed into "fall back and carry on."
Ratchet passes clean after the narrowing.

### Tests

- 6 new tests in `TestFindLatestDatabaseSurveyWithKey`
  (`tests/test_integration_registry_pg.py`, real Postgres): no-key, single-survey,
  **the exact 2026-09-26 bug shape** (older survey carries the key, newest doesn't —
  `test_the_2026_09_26_bug_shape_an_older_survey_carries_the_key_the_newest_does_not`),
  the falsy-candidate edge case, an end-to-end check through
  `_credential_capability_results` itself, and the dedup cache.
- `test_credential_totals_from_an_older_survey_still_show_when_the_latest_run_omits_the_probe`
  (the 2026-09-26 fix's own test) — verified green, unmodified, run explicitly and
  confirmed rather than assumed passing.

### Measured (direct call, `build_survey_results`, `schema_inventory` board, `laz_local_adventureworks`)

| | round 1 (before round 2) | round 2, before the redundant-call fix | round 2, final |
|---|---|---|---|
| fresh-instance call | 2.2-2.7s | 2.1-2.7s (5x real fetches, no dedup for the new method) | **420-770ms** |
| cProfile: time in `find_latest_database_survey_with_key`/`_uncached` | n/a (was `get_database_surveys`, 1.85-2.86s) | 2.1s (5 calls, all real) | **0.54s (1 real call, 4 cache hits)** |

## (b) The `list_projects` N+1 fix

### What profiling found — not what round 1 assumed

Round 1 wrapped `list_projects` in `asyncio.to_thread` to stop it blocking the event loop,
but did not address its own wall-clock cost. `cProfile` on the unbatched route (68
registered projects) found the dominant single cost was NOT `is_working_set_hidden`
(round 1's own thread-dump culprit) but `get_disposition(p.github_url)` — itself
`resolve_repo_entity_slug` → `get_by_github_url`, which runs **`SELECT * FROM projects`
and searches for a matching `github_url` in Python, once per project**: 0.93s of a 1.42s
total call.

### The fix

Three new batch registry methods, one query each instead of one-per-project:
- `get_dispositions_for_entities(entity_type, entity_slugs)` — `WHERE entity_slug IN (...)`.
- `get_working_set_hidden_for_entities(entity_type, entity_slugs, user_id=...)` — same
  per-user/shared-bucket resolution as the singular `is_working_set_hidden`, resolved once
  for the whole batch.
- `get_egeria_linkages_for_entities(entity_type, entity_slugs)`.

For dispositions specifically, the batch method keys directly on `p.slug` rather than
resolving through `github_url` at all — correct for `list_projects`'s only real use case
(already-imported projects, whose dispositions are reconciled onto the real slug at import
time, per `resolve_repo_entity_slug`'s own docstring), and what skips the expensive
full-table-scan-per-project entirely rather than merely batching it.

`web/routes/projects.py` gained `_list_projects_sync` (a new, batched summary builder used
only by the list route) and `_PrefetchedLinkageRegistry` — a small shim so the batched path
reuses `egeria_linkage.describe_publish_status`'s exact existing, already-tested logic
against a prefetched dict rather than duplicating "is this published" separately.
`_to_summary` itself is untouched and still used by `get_project` (one project — the right
shape for per-item registry calls, not an N+1).

### Tests

- `test_list_projects_keeps_each_projects_disposition_and_hidden_flag_distinct` and
  `test_list_projects_keeps_each_projects_publish_status_distinct`
  (`tests/test_web.py`) — several projects with DIFFERENT values on all three batched axes
  at once, checking the batch-then-join doesn't cross wires between projects (the failure
  mode a batch-and-join refactor risks: project B ending up with project A's data).
- `test_list_projects_off_the_loop.py` updated: its slow-call injection point moved from
  the now-unused singular `is_working_set_hidden` to the new batch method
  (`get_working_set_hidden_for_entities`) — same event-loop-not-blocked property, updated
  to match what the route actually calls now.

### Measured (direct call, `_list_projects_sync`, 65 registered projects)

| | before | after |
|---|---|---|
| call time | 1.42-2.8s | **10-20ms** (~100x) |

## A regression I introduced and fixed within this same round: `list_databases`

While running the coordinated real-HTTP measurement pass, a concurrent boot-sequence
burst caught `/api/auth/me` — zero database work — at **871ms**. `phase_timing` logs
showed `/api/databases/`, `/api/projects/`, and `/api/databases/.../survey-results/boards`
all bunched at the same ~870-965ms mark in that run, a pattern consistent with something
blocking the event loop rather than three independent slow routes.

`list_databases` (`web/routes/databases.py`) is `async def` and was NOT wrapped in
`asyncio.to_thread` — the same bug class round 1 fixed for `list_projects`, and the
`/questions` route before that (BY-ANALYSIS-PROGRESSIVE-AND-GRAPH-IMPLEMENTED.md §2a).
With only 3 registered databases in this environment it was never as catastrophic as
`list_projects`'s 68-project stall, which is presumably why round 1's SIGUSR1 thread dump
(taken during a `list_projects`-heavy burst) never surfaced it — but this round's OWN
`find_latest_database_survey_with_key` wiring into `databases.py`'s `_to_summary` added a
real synchronous query to that unwrapped path, making the existing gap newly visible under
measurement.

Fixed the same way as `list_projects`: body moved to `_list_databases_sync`, called via
`await asyncio.to_thread(...)`. New regression test, `tests/test_list_databases_off_the_
loop.py`, following `test_list_projects_off_the_loop.py`'s exact shape — verified failing
without the fix (git-stashed the route change, reran: fails, `/health` blocks for the full
injected sleep) and passing with it.

**Not batched** — `list_databases` still does per-database registry calls, just off the
event loop now. With only 3 databases this round, the absolute cost (~900ms-1s for the
whole route, per `phase_timing`) is smaller than `list_projects`'s pre-fix 68-project case,
but it is the same N+1 shape and was explicitly out of round 2's brief (which named
`list_projects`, not `list_databases`). Flagged here as a known, deliberately-left-unbatched
follow-up, not silently left unmentioned.

## Coordinated quiet measurement window

Per design's explicit request this round (round 1 used only an informal `pg_stat_activity`
spot-check, flagged as a gap): the PR/CI coordinator confirmed all peer sessions checked in
as not touching the shared Postgres registry, stopped PR/CI's own 8813 gate server, and
confirmed their own #348 fix agent's full-suite run had finished (via process check, not
assumption) before giving the go-ahead.

- **Window:** 2026-09-29T14:39:36Z – 2026-09-29T14:46:09Z (~6.5 minutes).
- **`pg_stat_activity` at window start:** 1 active (the check query itself), 74 idle.
- **`pg_stat_activity` at window end:** 1 active (the check query itself), 72 idle.
- Measured on a scratch `resource-explorer web` process, port 8899, never 8810/8813 — same
  posture as round 1.
- **One caveat this window did not cover, and round 1 didn't either: machine-level CPU
  contention.** `uptime` during measurement showed load averages of 17-25 and a
  long-running `uv run resource-explorer web` process (52 threads, ~2GB RSS) already on the
  box — almost certainly the owner's 8810 process with its "light run-queue polling" the
  coordinator named as an acceptable, unavoidable baseline, not something to wait out. The
  Postgres-level window was genuinely quiet; the machine was not fully idle. Board-summary
  timings below show real variance (462ms-1.9s across repeat calls of the identical
  request) that database quietness alone does not explain — reported as measured, not
  smoothed, with this caveat stated rather than treated as noise to average away.

## Gate results

1. **`/api/projects/` under 300ms with 68 projects.** **PASS.** Cold (fresh server, first
   call, includes one-time schema-init): 267ms. Warm: 8-53ms across repeat measurements.
   (65 projects registered in this environment, not exactly 68 — same order of magnitude,
   same N+1 pattern being tested.)

2. **`/api/auth/me` under 100ms** (carried over from round 1, re-verified this round under
   the coordinated window and under concurrent load including the newly-discovered
   `list_databases` gap before it was fixed). **PASS.** 1.6-2.4ms warm; stayed at 0.2-2.7ms
   even while `list_databases` was still blocking the event loop, confirming round 1's
   `list_projects` fix still holds and `list_databases`'s bug, once found, didn't touch
   this gate's own pass.

3. **A board-summary request under 200ms, in-server.** **NOT MET**, still, on
   `laz_local_adventureworks`. Measured (post-fix, same box, same window): 462ms-1.9s
   across repeat `schema_inventory` requests, median roughly 700-800ms — a real ~3-4x
   improvement over round 1's ~2-3s (and further still over the pre-round-1 baseline), but
   not under the 200ms bar. Two identified reasons, neither of them the fetch pattern this
   round targeted (that part is fixed — see the profiling table above):
   - The one real `jsonb_exists` query itself measured 300-500ms in isolated repeat
     testing even on a quiet DB — Postgres still has to scan-and-cast 57 rows' `survey_data`
     text to jsonb per query (no index on the expression), it just no longer transfers all
     57 full blobs back to Python. A functional/GIN index on `(survey_data::jsonb ->
     'credential_capability')` or a proper `jsonb` column would likely close most of this
     remaining gap, but is a schema-migration-scale change not attempted this round.
   - `schema_inventory` specifically also does real, necessary data-volume work unrelated
     to credential_capability (61 tables' worth of column/constraint enumeration via
     `query_detail_rows`) — most OTHER boards measured well under 200ms this round
     (`privilege_audit` 27-106ms, `db_activity_signals` 76ms, `db_resilience`/`db_external_
     dependencies` 28ms), so this is not a blanket "all boards are slow" finding.
   - **A separate, much larger, out-of-scope bottleneck found while checking other
     boards**: `db_classification` measured **~2.4s** via direct-call `cProfile` — a
     completely different code path (`db_derived.run_db_derived`), 398 raw `cursor.execute()`
     calls in one board read, `query_detail_rows` alone called 182 times. This is not the
     survey_data-blob pattern round 2 targeted at all; it's the same class of N+1 that
     `laz_local_adventureworks`'s OWN board-summary-read-cost branch (a separate, unmerged
     PR) already measured pre-its-own-fix at similar magnitudes for other boards
     (`db_classification` 25.8s, `db_relationship_graph` 12.2s, `db_fingerprint` 10.4s, per
     that branch's own IMPLEMENTED doc). Flagged, not fixed — clearly a round-3-or-later,
     out of this round's explicit brief (survey_data fetch + `list_projects`).

4. **Cold-load By-analysis headlines under 2s.** **NOT MET.** Boot sequence (concurrent
   `auth/me`, `projects`, `activity`, `databases`, boards-catalog) + first 3 boards
   (matching the client's own `BY_ANALYSIS_MAX_CONCURRENT_READS=3` bound): 2.3-2.9s across
   repeat measurements this window. Root cause is gate item 3's unmet remainder plus
   machine-load variance noted above — not a new, separate finding. `/api/auth/me` and
   `/api/projects/` both individually pass their own gates comfortably; the boot sequence's
   total is dominated by the same `schema_inventory`/`row_count_snapshot`-class board cost.

## Full suite

`uv run pytest tests/ -q -rf`, clean env, after the ratchet fix and the `list_projects`/
`list_databases` off-the-loop test updates: **6837 passed, 103 skipped, 0 failed**
(15m04s) — final run, after the `list_databases` fix and its new regression test both
landed.

## Summary of what's still open, for a future round

- `db_classification`'s `db_derived`-engine N+1 (~2.4s, 398 queries in one board read) —
  the actual dominant remaining bottleneck for a full "every board under 200ms" claim, and
  clearly out of this round's scope.
- A functional/GIN index (or schema change) to get the one real `jsonb_exists` query itself
  under, say, 100ms on a heavily-surveyed database — the remaining piece of gate item 3
  even after this round's dedup/query-shape fix.
- `list_databases`'s own per-database N+1 (fixed for event-loop-blocking this round, not
  batched) — low absolute impact with only 3 registered databases here, but the same shape
  as `list_projects`'s round-2 fix and would scale the same way if the database count grew.
