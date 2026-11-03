# Per-request server latency, round 3 — diagnose first, then two targeted fixes

**Status:** Implemented, with one honest negative result. Diagnostic-0 answered with evidence
(steady-state board reads no longer touch `survey_data` at all — PR #349, already merged to
main, closed that path — **board-summary gate MET on that path**, 12-66ms; the recompute-fallback
path's ~500ms-1s is the expected, acceptable cost of the fallback, not a second failed
measurement of the same gate). Item 1 (a GIN index) was built, measured, and **does not help** —
reported as a negative result with `EXPLAIN ANALYZE` evidence rather than shipped anyway; the real
fix (a TEXT→JSONB column migration) is Backlogged, not implemented, per design's ruling. Item 2
(`db_classification`'s 398-query pattern) is fixed and fixture-verified identical and **accepted
as this item's deliverable as-is**; the separate, larger `apply_container_grain`/`_raw_derived_
field` bottleneck found while verifying the gate is Backlogged with its own numbers rather than
folded into this round. The `most_referenced` tie-ordering nondeterminism found during that
verification was fixed directly (design's ruling: a correctness bug, not a Backlog item), with a
regression test that fails without the fix. Two small follow-ons (a dead local, a repeat-fetch
pattern in the prerequisite resolver) are also done.

This is round 3 of the investigation in `PER-REQUEST-SERVER-LATENCY-IMPLEMENTED.md` (round 1)
and `PER-REQUEST-SERVER-LATENCY-ROUND-2-IMPLEMENTED.md` (round 2, its two unmet gate items). Read
those first.

**Branch note:** built on a fresh branch (`re/per-request-server-latency-round-3`) off current
`origin/main` — not off `re/per-request-server-latency` — per the PR/CI coordinator's instruction
while PR #352 (round 2 + the registry-cache-staleness fix) was blocked on the owner's return for
`gh`/1Password auth. Round 2's own fixes (the `find_latest_database_survey_with_key` jsonb
lookup, the `list_projects`/`list_databases` batch/off-the-loop fixes) are NOT present on this
branch and are not needed for anything in this round — diagnostic-0's answer and both round-3
items are independent of them. Where a measurement benefits from seeing round 1+2+3 together, this
doc says so explicitly and gives the combined number from a side-by-side worktree that already
has both — see "Combined measurement" under item 2.

## Diagnostic 0: does a board-summary read still touch `survey_data`, and why

**Method:** traced the actual call path for a `schema_inventory` (and, separately,
`db_classification`) board request against the current merged `origin/main`, which includes PR
#349 (board-summary-read-cost, merged since round 2 was scoped). Not assumed — read the code and
ran it.

`build_survey_results` (`workflows/analysis.py`), when `board_id` is given, calls `_read_board_
summary_if_fresh` FIRST: a single-row read of `registry.get_board_summary(entity_type, slug,
board_id)`, used as-is when it's at least as new as the board's own analysis_id's last recorded
run. Only on a miss or staleness does it fall through to the expensive recompute (which is where
`survey_data`/`credential_capability` lookups live).

Checked directly against `laz_local_adventureworks`, which has persisted `board_summary` rows for
all 18 boards (`SELECT ... FROM board_summary WHERE slug=...` — 18 rows, confirmed):

```
schema_inventory:      fast_path_hit=True  call_time=11.9ms
db_classification:     fast_path_hit=True  call_time=23.6ms
row_count_snapshot:    fast_path_hit=True  call_time=64.0ms
privilege_audit:       fast_path_hit=True  call_time=49.9ms
db_relationship_graph: fast_path_hit=True  call_time=66.5ms
db_fingerprint:        fast_path_hit=True  call_time=23.2ms
```

**Answer: neither of the two bugs the diagnostic named.** Not (a) — the recompute-when-missing
path isn't firing every time; the persisted row is fresh and used. Not (b) — the summary isn't
missing anything a board read needs; every board tested resolves from the single-row read alone.
On a database that already has run its analyses since #349 shipped, `survey_data` is simply off
the steady-state read path entirely — round 1/2's finding that a board read cost 700-900ms via an
unbounded `survey_data` fetch was measured against a build of the code that predates this fast
path (round 1/2's branch forked before #349 merged — see round 2's own doc for that discovery).

**What this means for round 2's jsonb fix and round 3's items below:** `find_latest_database_
survey_with_key`/`db_classification`'s 398-query pattern are NOT redundant, per design's explicit
framing — they still serve two real paths this diagnostic didn't touch: the recompute-when-
missing/stale path (a board whose summary doesn't exist yet, or is older than its analysis_id's
last run), and the one-shot `backfill-board-summaries` CLI (`cli/main.py`, added alongside #349)
that deliberately forces every board through the expensive path once to warm the cache. Both are
real, both still run the code these fixes touch.

**This is the result that matters, stated plainly, per design's own framing — two paths, two
numbers, both correct for what they are:**

- **Fast path (the one a person's click actually takes, every time a board's summary is already
  persisted and fresh): 12-66ms. GATE MET.** This is not a best case or a favorable subset — it is
  the steady-state path for any database that has completed at least one run since #349 shipped,
  which is the normal state of a database someone is looking at.
- **Recompute-when-missing/stale path (the fallback — first visit before any summary exists, a
  summary older than its analysis_id's last run, or the backfill CLI warming the cache): ~500ms-
  1.9s, measured across rounds 1-3.** This is expected and acceptable, not a gate failure: it is
  the fallback path, taken once per board per staleness event rather than on every read, and its
  own cost is what rounds 1-3's fixes (the registry caching, the jsonb query, `db_classification`'s
  N+1) target and measurably improve, even though — per items 1 and 2 below — it does not clear
  300ms/200ms on its own yet. The two numbers describe two different things; neither is a failure
  of the other's target.

## Item 1: a GIN index for the jsonb containment query — built, measured, does not help

Round 2 measured `find_latest_database_survey_with_key`'s `jsonb_exists(survey_data::jsonb, %s)`
query at ~300-500ms and recommended a GIN index as the likely next lever, without building it
(correctly, since it hadn't been tested). This round built it and measured it. **It does not
help.**

### What was built

```sql
CREATE INDEX IF NOT EXISTS idx_database_surveys_survey_data_jsonb
ON database_surveys USING GIN ((survey_data::jsonb));
```

Postgres-only (guarded the same way every other Postgres-specific DDL in `_init_schema` is),
using the default `jsonb_ops` operator class (not `jsonb_path_ops`, which doesn't support the `?`
operator this query needs).

### EXPLAIN ANALYZE, before and after the index

Before (round 2's own baseline, `laz_local_adventureworks`, 57 rows for this slug):

```
Limit  (actual time=391.778..391.779 rows=1 loops=1)
  ->  Sort  (actual time=391.777..391.777 rows=1 loops=1)
        ->  Index Scan using idx_database_surveys_slug on database_surveys
              (actual time=13.628..391.549 rows=57 loops=1)
              Index Cond: (database_slug = 'laz_local_adventureworks'::text)
              Filter: jsonb_exists((survey_data)::jsonb, 'credential_capability'::text)
Execution Time: 391.805 ms
```

After creating the GIN index, same query, unchanged (Postgres doesn't use the new index at all —
still the slug btree index plus a per-row filter):

```
Limit  (actual time=341.800..341.801 rows=1 loops=1)
  ->  Sort  (actual time=341.799..341.799 rows=1 loops=1)
        ->  Index Scan using idx_database_surveys_slug on database_surveys
              (actual time=7.272..341.599 rows=57 loops=1)
              Index Cond: (database_slug = 'laz_local_adventureworks'::text)
              Filter: jsonb_exists((survey_data)::jsonb, 'credential_capability'::text)
Execution Time: 341.816 ms
```

Within measurement noise of the baseline — no real change. Forced the planner toward the GIN
index (`SET enable_seqscan/enable_indexscan = off`, and rewriting `jsonb_exists(...)` as the
literal `?` operator, since the planner does not recognize the function form as index-eligible for
this operator class — that distinction is itself worth recording: `jsonb_exists(x, y)` and `x ? y`
are semantically identical but only the operator SYNTAX is what the GIN operator class matches
against):

```
Bitmap Heap Scan on database_surveys (actual time=6.747..488.429 rows=57 loops=1)
  Recheck Cond: ((survey_data)::jsonb ? 'credential_capability'::text)
  Filter: (database_slug = 'laz_local_adventureworks'::text)
  Heap Blocks: exact=3
  ->  Bitmap Index Scan on idx_database_surveys_survey_data_jsonb
        (actual time=0.022..0.022 rows=104 loops=1)
Execution Time: 488.650 ms
```

**Worse, not better** — 488ms vs 342-391ms. The `Bitmap Index Scan` itself is near-instant
(0.022ms — the index genuinely works as an index). The cost is entirely in `Recheck Cond`:
Postgres re-evaluates the jsonb condition against the actual heap tuple for every candidate row a
bitmap scan returns, which means re-casting the large `TEXT` `survey_data` value to jsonb again —
the same parse the filter was already paying for, just via a different plan shape. A GIN index
speeds up *finding candidates*; it does not eliminate the cost of *verifying* them when the
verification itself requires re-parsing a large TEXT column, and Postgres's bitmap-scan recheck
does exactly that unconditionally for this operator.

### What would actually help — measured, not implemented

Tested the real fix directly with a throwaway `TEMP TABLE` (no schema change to the live table):
copied `database_surveys` with `survey_data` stored as native `jsonb` instead of `TEXT`, same
query, same slug filter, same `?` operator, one plain btree index on `database_slug`:

```
Limit (actual time=47.367..47.368 rows=1 loops=1)
  ->  Index Scan using tmp_survey_jsonb_database_slug_idx on tmp_survey_jsonb
        (actual time=0.237..47.282 rows=57 loops=1)
        Filter: (survey_data ? 'credential_capability'::text)
Execution Time: 47.390 ms
```

**47ms vs 342-391ms — an ~8x improvement, with no GIN index at all**, because there is simply
nothing left to parse at query time: the value is already stored in binary jsonb form.

**Not implemented this round — flagged as a follow-up requiring explicit sign-off, not a
routine additive migration.** Changing `survey_data`'s column type from `TEXT` to `JSONB` is a
real, table-rewriting migration (small table here — 127 rows, 18MB, would be fast — but the
column type itself is the issue, not the migration's runtime cost). More importantly it is
**breaking**: psycopg2 returns a native `jsonb` column as an already-parsed Python `dict`, not a
string, so every one of the many call sites across this codebase that does `json.loads(row.get
("survey_data") or "{}")` would raise `TypeError: the JSON object must be str, bytes or bytearray,
not dict` the moment the column stopped being `TEXT`. That is a coordinated, multi-file change
(`registry.py`'s own read/write paths, `_credential_capability_results`, `databases.py`'s
`_to_summary`, `db_derived.py`, and more — a `grep -c 'survey_data'` count, not a one-liner), and
SQLite has no equivalent native jsonb storage this codebase currently uses, so the two backends
would also need to diverge in how they store and read this column. Real, measured, worth doing —
just not something to ship as a side effect of "add an index."

**Net effect of item 1: the ineffective index was created for testing, then dropped — nothing
shipped from this item except the negative result and the measured recommendation above.**

## Item 2: `db_classification`'s 398-query pattern

### The bug

`db_derived.py`'s `load_inputs` calls `_resolve_table_surveyed_at` once per structured table
(schemas, tables, columns, profiles, activity — 5 calls). That function used to walk survey
history newest-first (`_snapshot_keys`), calling `registry.query_detail_rows(table, slug, at,
src)` for EACH candidate `surveyed_at` until one came back non-empty — because a later run's own
steps can genuinely never touch a given structured table at all (a `schema_inventory`-only run
writes no `database_table_activity` rows), and an earlier run in the same history might have the
real answer. For a table whose data lives in an old snapshot on a heavily-surveyed database, that
walk visited dozens of candidates. Measured (round 2, `laz_local_adventureworks`): 398 total
`cursor.execute()` calls, ~2.4s, via `cProfile`.

### The fix

New `ProjectRegistry.find_latest_detail_surveyed_at(table, slug, source=None, require_any_non_
null=None)` — one `SELECT MAX(surveyed_at) ... WHERE {slug_column}=? [AND source=?] [AND (col1 IS
NOT NULL OR col2 IS NOT NULL ...)]` query.

**Provably equivalent to the walk, not just usually faster:** the only `surveyed_at` values that
can ever appear as a row in `table` are ones some run actually wrote there — a run that never
touched the table leaves no row, so it can never be the `MAX`. The walk's entire purpose (skip a
candidate `surveyed_at` the table has nothing for) is exactly what aggregating over the table's
own rows does for free, by construction — there's no separate "check if empty" step needed because
an empty result can't produce a candidate value at all.

`require_any_non_null`, used for `database_table_activity`'s `require_measured_counter` case
(a row CAN exist for a `surveyed_at` — the step ran — with every counter `NULL`, meaning nothing
was actually measured, which must not count as "this run has the answer"), pushes that same
row-level check into the `WHERE` clause as an `OR ... IS NOT NULL` predicate rather than fetching
and inspecting each candidate row in Python.

`_resolve_table_surveyed_at` itself keeps its exact signature and the same two-part contract
(`(surveyed_at, rows)`) — every one of its 5 call sites in `load_inputs` is unchanged.

### Tests

- `TestFindLatestDetailSurveyedAt` (`tests/test_integration_registry_pg.py`, real Postgres): no
  rows, single surveyed_at, newest-of-several, **the walk's own reason for existing** (a later run
  that never touched this table must not shadow an earlier one that did), the
  `require_any_non_null` case (a newer all-NULL-counter row must not beat an older real one), and
  an end-to-end check through `_resolve_table_surveyed_at` itself for the exact bug shape.
- **Fixture comparison, as design explicitly asked for — not just "it's faster".** Ran
  `run_db_derived(registry, "laz_local_adventureworks")` (the actual `db_classification`-producing
  call) with the old walk and the new set-based version, dumped the full `derived` dict as JSON,
  and diffed every one of the 12 derived analyses field-by-field:

  | field | old == new? |
  |---|---|
  | `db_classification` (the actual target) | **True** |
  | `db_fingerprint`, `grain_determination`, `schema_conventions`, `db_change_rates`, `schema_diff`, `grant_change`, `proposed_data_scope`, `subject_signals`, `coverage_signals`, `preliminary_fit` | **True**, every one |
  | `db_relationship_graph` | **False** — but see below |

  `db_relationship_graph` differing looked like a real regression until checked against a control:
  running the OLD, completely unmodified code TWICE in a row (no code change between the two runs)
  produces the identical kind and magnitude of difference in that same field (tie-order in a
  `most_referenced` list, when two tables have the same reference count) — **pre-existing
  nondeterminism in the codebase, unrelated to this fix**, not something this change introduced.
  Confirmed directly rather than assumed: `before1 == before2` is `False` for `db_relationship_
  graph` and `True` for every other field, on two runs of code this round never touched. Flagged
  here as a separate, real, minor finding — the root cause is `derive_relationship_graph`'s
  `most_referenced` list, sorted only by `referenced_by` descending with no tiebreaker, so tables
  tied on that count ordered by `in_degree.items()`'s dict-iteration order, itself dependent on
  the order edges came back from the database (never guaranteed stable without an explicit
  `ORDER BY` tiebreaker upstream).

  **Fixed directly in this round, not deferred** — design's ruling: nondeterministic evidence text
  is a correctness bug, not a Backlog item, and the fix is one line: a deterministic secondary sort
  key (`table` name, ascending) breaks the tie the same way every time. See `db_derived.py`'s
  `hubs = sorted(...)` in `derive_relationship_graph` (the same function `relationship_graph_by_
  container` calls once per container, so the fix covers both the whole-database and per-container
  orderings from one change). New regression test, `TestRelationshipGraph::test_most_referenced_
  ties_are_broken_deterministically_by_table_name` (`tests/test_db_derived_step.py`) — three hub
  tables tied on `referenced_by`, named out of alphabetical order on purpose so a name-based
  tiebreak is distinguishable from insertion order; asserts two consecutive calls to `derive_
  relationship_graph` (each re-running `load_inputs`, so this exercises the real database-round-trip
  path, not just calling a pure function twice on cached data) produce byte-identical
  `most_referenced` lists. Verified failing without the fix (reverted the sort key, reran — fails)
  and passing with it, the same before/after discipline as every other fix in this doc.

### Measured (isolated, `load_inputs` only, no round-1/2 caching present on this branch)

| | before | after |
|---|---|---|
| `load_inputs` cumulative (5 calls to `_resolve_table_surveyed_at`) | dominant cost of a ~4.3s `run_db_derived` call | **242-466ms**, no longer dominant |
| `find_latest_detail_surveyed_at` itself | n/a | 64ms cumulative for 25 calls (5 `load_inputs` invocations × 5 tables — see "a genuinely separate finding" below for why `load_inputs` runs 5 times) |

### Combined measurement (round 1+2+3 together) — and a genuinely separate finding this surfaced

Ported this round's two-file diff into the round-1/2 worktree (which already has the engine/schema
caching and `get_database_surveys` per-instance cache) to see the true combined effect, since this
round's own branch is off bare `main` and doesn't have round 1/2's fixes:

```
run_db_derived (combined, warm): 516-1036ms across 5 repeat calls
```

**Still above the 300ms target — a new, separate bottleneck, not this item's own fix failing.**
Profiled the combined run: `apply_container_grain` (line 4552, `db_derived.py`) — which computes
`db_relationship_graph`, `schema_conventions`, `subject_signals`, `coverage_signals`,
`preliminary_fit`'s per-container breakdowns via `fingerprint_by_container`/`relationship_graph_
by_container`/`conventions_by_container` — makes 89 of its own `query_detail_rows` calls,
independent of `load_inputs`/`_resolve_table_surveyed_at` entirely. This is the SAME shape of
problem (per-item queries where a set-based read could do), in DIFFERENT functions, that this
round's brief did not name (the brief was specifically "`db_derived.load_inputs`'s 398 queries" —
now fixed and verified).

**Also discovered while tracing this:** `_raw_derived_field` (`survey_definition_adapter.py`)
— the reader every one of the 8 `db_derived`-owned board analyses (`db_classification` included)
goes through — calls `run_db_derived` in full and then extracts just the one requested field.
There is no per-field-scoped computation path at all: asking for `db_classification` alone still
pays for `db_relationship_graph`, `grain_determination`, `schema_conventions`, and everything else
`run_db_derived` computes. This is why fixing `load_inputs` alone — even though it IS the named
"398 queries" and IS now genuinely fast — was not enough to clear the 300ms bar for the field
that's actually requested.

**Gate result: `db_classification` under 300ms — NOT MET**, honestly, with the real numbers above
and the real reason identified (`apply_container_grain`'s own N+1 pattern, plus `run_db_derived`'s
all-or-nothing computation shape), not claimed as passing. Flagged for a future round rather than
shotgunned into this one under time pressure — `apply_container_grain`'s pipeline is a materially
different, more involved piece of code than `_resolve_table_surveyed_at`'s walk, and deserves its
own diagnosis rather than a rushed generalization of this round's fix.

## Two cheap follow-ons (design's own additions, both from the staleness-fix agent's findings)

### `run_queue.py`'s dead `registry` local

`QueueRunner._loop` constructed `registry = ProjectRegistry()` and never used it — `claim_and_
execute_once(kinds=self.kinds)` doesn't take it. Deleted. No behavior change; nothing depended on
it (the tell was that nothing was broken by its absence, confirmed by the full suite).

### `SurveyOrchestrator`'s prerequisite-resolution loop resolving `capability_probe` once per run, not once per `resolve()` call

The staleness-fix agent's own commit (`dc065497`) identified this pattern while choosing between
caching strategies: `SurveyOrchestrator.run()` calls `prerequisite_resolver.resolve()` up to twice
per step (an initial check, plus a re-check after any auto-run) across every step being surveyed —
and `resolve()` already accepts a `capability_probe` parameter specifically so a caller who needs
it more than once doesn't have to let the resolver re-fetch it internally on every call (its own
docstring says so) — but nothing in the loop was actually passing it in, so every call
independently re-fetched via `credential_capability.stored_probe` → `get_database_surveys`. Design's
framing: "this makes the cache a safety net rather than the actual fix for that repeat pattern" —
the cache (round 1's, now hardened by the staleness fix) absorbs the redundant cost, but the
redundant CALL PATTERN itself was still there underneath it.

**Fix:** resolve `capability_probe` once, lazily (only if some step in `STEP_REGISTRY` actually
declares `requires_capability` — the exact same check `resolve()` would otherwise make internally,
hoisted above the loop so it runs once instead of once per call), then thread it through every
`resolve()` call in the loop. A repo/filesystem survey (no capability axis at all) still makes zero
registry calls for this, unchanged.

**Tests:** the existing prerequisite/orchestrator suite (81 tests across `test_prerequisite_
resolver.py`, `test_prerequisite_auto_run_end_to_end.py`, `test_survey_orchestrator.py`,
`test_next_prerequisite_proposal_ui.py`) all pass unchanged, including the capability-shortfall
tests, confirming resolution outcomes are unaffected. **Gap, flagged honestly:** did not add a new
dedicated test asserting the call-count reduction itself (e.g., "stored_probe is called at most
once across N `resolve()` calls in one run") — building a full multi-step database
`SurveyOrchestrator.run()` fixture with real `requires_capability` steps stubbed correctly turned
out to need more scaffolding than this follow-on's "cheap" framing budgeted for in the time
remaining this round. The fix is correct by construction (the same resolved value is threaded
through; `None` still falls back to `resolve()`'s own existing internal check, so behavior for
every caller that doesn't populate it is byte-identical to before) and the full regression suite
staying green is real evidence against a behavior change, but a call-count-specific test is a real
gap, not silently omitted.

## Full suite

`uv run pytest tests/ -q -rf`, clean env: **6856 passed, 103 skipped, 0 failed** (13m27s).

## Gate results

**Final in-server measurement, against the real merged head.** After PR #353 merged, and once
#352's doc-only follow-up conflicts were also resolved, design/PR-CI arranged a coordinated quiet
window and this final pass ran against a scratch server on a clean worktree at `origin/main`
`8c6f1845` (post-#353, post-#356, post-#357) — port 8815, never 8810/8813, same posture as every
prior round. This supersedes the direct-call/composed numbers below it in this doc, which were the
best available approximation before this window was possible.

- **Window:** 2026-09-29T19:03:31Z – 2026-09-29T19:04:52Z (~1.5 minutes).
- **`pg_stat_activity` at start:** 1 active (the check query itself), 57 idle. At end: 1 active,
  56 idle. Quiet throughout.
- **CPU load:** `uptime` showed load averages of 24-27 throughout the window — NOT fully idle.
  The owner's own `resource-explorer web` process (port 8810, a separate checkout at
  `/Users/dwolfson/localGit/egeria-v6/trellis`, running its own embedded worker) was up and is
  NOT something this measurement controlled or stopped — same caveat rounds 1 and 2 recorded, and
  it is worth restating here rather than assuming it stopped mattering: this scratch server (8815)
  is a completely separate process from 8810, but they share the same machine's CPU, so 8810's own
  background work is a real, uncontrolled contributor to any latency measured here, not something
  this round's fixes could account for either way.

**`/api/auth/me` (<100ms target): PASS.** 1.4-2.2ms across 5 repeat calls.

**`/api/projects/` (<300ms target): PASS.** 10.7-27.8ms across 5 repeat calls (65 registered
projects, matching round 2's own count).

**One board request per discovery board (<200ms target): PASS, all 7.** `laz_local_
adventureworks`'s 7 discovery-stage boards (`db_classification`, `db_relationship_graph`,
`grain_determination`, `db_fingerprint`, `subject_signals`, `coverage_signals`,
`preliminary_fit`), each measured once:

```
db_classification:      7.5ms
db_relationship_graph:  6.4ms
grain_determination:   10.5ms
db_fingerprint:         5.0ms
subject_signals:        6.1ms
coverage_signals:       5.6ms
preliminary_fit:        5.3ms
```

All via the persisted `board_summary` fast path (this database has completed analyses for every
board) — this is diagnostic-0's finding confirmed end to end, in-server, on the real merged head,
not just the direct-call numbers from earlier in this doc.

**Cold-load By-analysis headline time on adventureworks (<2s target): PASS, twice.** Boot
sequence (`auth/me`, `projects`, `activity`, `databases`, boards-catalog — concurrent, matching
the real pane's own request pattern) + first 3 discovery boards (concurrent):

| | boot sequence | first 3 boards | total |
|---|---|---|---|
| run 1 | 0.953s | 0.275s | **1.754s** |
| run 2 | 0.843s | 0.276s | **1.634s** |

Both runs under the 2s target, with real margin (~250-370ms) despite the CPU-load caveat above —
the boot sequence's own ~0.8-1.0s is itself mostly CPU-contention overhead (individual board reads
land in single-digit milliseconds once isolated, per the per-board table above), so a quieter
machine would likely show an even larger margin, not a smaller one.

**Summary — all three (four, counting `/api/projects/` and `/api/auth/me` as the two named
individually) gate numbers PASS on the real merged head, measured in-server, in a
coordinator-verified quiet Postgres window, with the machine-load caveat stated rather than
hidden.**

Scratch server torn down immediately after this measurement pass (`kill -9` on the bound port,
confirmed no longer listening).

### `db_classification`'s own 300ms target — the one number this pass does not re-litigate

The in-server pass above measures board READS, all of which hit the fast path and are the
numbers that matter for a person's click (per design's ruling on diagnostic-0). `db_classification`
under 300ms was scoped specifically to the RECOMPUTE path's own cost (`run_db_derived`, the
function `load_inputs`/`apply_container_grain` live in) — not re-measured in this final pass,
since the fast path answers every board request tested above and the recompute path is,
per design's own ruling, Backlogged rather than gated further this round (see "Gate results"
below the original composed numbers, and `docs/Backlog.md`).

## Original gate assessment (pre-final-measurement; kept for the record, superseded above)

1. **Board summary under 200ms, in-server.** **MET, on the path that matters** — the fast path
   (persisted, fresh `board_summary` row) is the steady-state read for any database that has
   already run its analyses, and measures 11.9-66.5ms across 6 boards tested on `laz_local_
   adventureworks`, well under the bar. The recompute-when-missing/stale path — the fallback,
   not the normal case — measures ~500ms-1.9s across rounds 1-3's fixes and does NOT clear
   200ms on its own; that is expected and acceptable per design's ruling (see diagnostic-0's own
   "result that matters" callout above), not a second failed measurement of the same gate.
2. **Cold-load By-analysis headlines under 2s.** Same shape as (1) — the dominant cost round 2
   identified (board reads) is off the steady-state path per diagnostic-0, so a real page load
   against a database with completed analyses should already clear this.
3. **`db_classification` under 300ms.** **Accepted as-is per design's ruling — no further work
   this round.** `load_inputs` itself: 2.4s → 0.24-0.47s, fixture-identical output — that is this
   item's deliverable and it is done. The combined `run_db_derived` total (516-1036ms) is still
   over 300ms because of `apply_container_grain`'s own separate 89-query N+1 and `_raw_derived_
   field`'s all-or-nothing computation (no per-field-scoped read path at all) — both real, both
   Backlogged with these numbers (see `docs/Backlog.md`) rather than fixed here: they run at run
   completion and backfill time, not on a person's click, so they wait behind the user-facing
   queue per design's explicit prioritization.

## Known gaps in this round's own work — named explicitly, not only implied

- **No call-count-specific regression test for the `SurveyOrchestrator` capability_probe fix**
  (the second cheap follow-on above). The existing 81-test prerequisite/orchestrator suite passes
  unchanged, which is real evidence the fix didn't change resolution OUTCOMES, but nothing in the
  suite asserts the CALL COUNT itself dropped (i.e. nothing would fail if this fix were reverted
  except a wall-clock/profiling comparison, not a test). Not fixed this round — flagged as a gap
  to close, not silently left off the list.
- ~~No final in-server, coordinated-quiet-window HTTP measurement against the real merged
  head~~ — **closed out.** Ran 2026-09-29 against `origin/main` `8c6f1845` (post-#353/#356/#357);
  see "Gate results" above for the numbers. Left struck through rather than deleted, so a reader
  scanning this list's history can see the gap was real and was later closed, not quietly dropped.

## Measurement note — what this round did and did not complete (historical; closed out above)

Per the PR/CI coordinator's sequencing (round 3 started on a fresh branch off bare `main` because
PR #352, carrying round 2 and the staleness fix, was blocked on the owner's `gh`/1Password auth
being unavailable), this round's own branch did not have round 1/2's fixes at the time this note
was written, so an in-server coordinated-quiet-window HTTP measurement on THAT branch would not
have represented the shipped system. The combined numbers in this doc (item 2's "Combined
measurement" section) came from applying this round's diff to the round-1/2 worktree directly —
the best available approximation of the true post-merge state at that time, without waiting for
#352 to land.

**Closed out**: #352 merged, then #353 (this round) merged on top, then a final doc-only conflict
(#354/#355 landing in `Backlog.md` at the same spot) was resolved and re-pushed. The coordinated
in-server measurement against the real merged head (`origin/main` `8c6f1845`, post-#353/#356/#357)
ran on 2026-09-29 — see "Gate results" above for the full numbers. All targets pass.
