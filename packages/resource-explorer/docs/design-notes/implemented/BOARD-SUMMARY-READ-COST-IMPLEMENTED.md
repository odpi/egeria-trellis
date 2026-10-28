# Board-summary read-cost fix — implemented

**Scope:** the By-analysis panel's progressive-render slice (PR #344/#346,
`re/by-analysis-progressive-and-graph`) fixed render ORDER — the contents board
paints immediately, cards stream in via a bounded queue — but not the underlying
READ COST. This change fixes the read cost itself, plus a design-added safety
net for the case where a board still doesn't settle in time.

**Branch:** `re/board-summary-read-cost`, built in an isolated worktree
(`/Users/dwolfson/localGit/egeria-v6/trellis-re-board-read-cost`), never in the
shared checkout. Based on `origin/re/by-analysis-progressive-and-graph` (which
this task's `board_id`-scoped `build_survey_results` depends on directly and
was not yet merged when this branch started) merged with `origin/main`
(`e88657ed`, includes #345 engine-note-persist) — see "Base branch" below for
why.

**PR:** not opened by this session — the PR/CI session batches these.

## The problem (found live, 2026-09-28, owner gate check)

`build_survey_results(board_id=...)` (`resource_explorer/workflows/analysis.py`),
the per-board read PR #346's progressive pane calls once per board, recomputes
its answer from scratch on **every call** — invoking that analysis_id's
`results_reader`/`headline_reader`, which read/aggregate from stored rows on
every invocation. Measured directly against the shared registry, serially, no
web server, on adventureworks discovery boards:

| Board | Measured (serial, no contention) |
|---|---|
| `db_classification` | 25.8s |
| `preliminary_fit` | 13.0s |
| `coverage_signals` | 12.8s |
| `db_relationship_graph` | 12.2s |
| `subject_signals` | 11.1s |
| `db_fingerprint` | 10.4s |
| `grain_determination` | 9.7s |

In the live pane, all 7 boards plus a `getQuestions` call fire in parallel
against the same registry and contend with each other (`BY_ANALYSIS_MAX_CONCURRENT_READS`
bounds it to 3 concurrent reads, but each one is independently this expensive),
so nothing settled within 60+ seconds on a real gate check, and a subsequent
owner check reported **5+ minutes** to settle.

## The fix

**Persist each board's computed dashboard dict as a `board_summary` row at run
completion**, and read it back on the fast path instead of recomputing.

### Schema (additive)

`resource_explorer/registry.py` — new `board_summary` table, same
`CREATE TABLE IF NOT EXISTS` + `_get_table_columns` migration pattern this file
already uses for `step_runs.flow_run_id`/`dispatch_failed` (PREFECT-DISPATCH-HONESTY):

```sql
CREATE TABLE IF NOT EXISTS board_summary (
    entity_type   TEXT NOT NULL,
    slug          TEXT NOT NULL,
    board_id      TEXT NOT NULL,
    summary_json  TEXT NOT NULL DEFAULT '{}',
    source_run_at TEXT DEFAULT '',
    computed_at   TEXT NOT NULL,
    PRIMARY KEY (entity_type, slug, board_id)
)
```

`summary_json` is the exact dashboard dict `build_survey_results(..., board_id=X)`
already returns for one board — not a redesigned/smaller shape — so a read can
hand it back verbatim. `source_run_at` (the analysis's `last_run_at`, from
`registry.get_analysis_last_run`) is the staleness anchor, deliberately
separate from `computed_at` (when the row was written) — see the table's own
comment in `registry.py` for why they can legitimately disagree.

Two new registry methods: `write_board_summary(entity_type, slug, board_id,
summary, source_run_at="")` and `get_board_summary(entity_type, slug, board_id)
-> dict | None`. A registry with no rows in this table behaves exactly as
before — every read falls back to the pre-existing expensive path — so this
table shipping empty is invisible until a writer populates it.

### Read path

`build_survey_results(..., board_id=...)`: when `board_id` is given, tries
`_read_board_summary_if_fresh` first — a single-row `get_board_summary` read,
compared against `registry.get_analysis_last_run` for the board's own
analysis_id(s) (a repo dashboard's `board_id` maps to several analysis_ids via
`SURVEY_RESULT_DASHBOARDS`; database/filesystem is 1:1). Used as-is when fresh
(same `stage`/`include_empty` filtering the expensive path applies, so a fast
answer never disagrees with what the slow one would have returned); falls
through to the existing expensive recompute when the summary is missing,
stale, or belongs to a `board_id` this reader doesn't recognise.

**Self-healing on a cold/stale read:** whenever the expensive path DOES run
with a `board_id`, the freshly-built dict is persisted before returning
(`_persist_board_summary`), so the very next read of that board is fast even
if no writer had ever run for it. No backfill step needed.

**Stage filter moved before `_read_analyses`** in the database/filesystem
synthesized-dashboard loop — the second half of the fix, independent of
`board_id`. The old order called the expensive reader for every analysis_id in
`results_map` (including every other stage's) before checking whether this
one's stage even matched. Harmless when `board_id` scopes the loop to one
iteration (the common progressive-pane case), but wasteful for a stage-wide
call with no `board_id`, which paid the full read cost for every OTHER
stage's boards too before discarding them.

### Write path

`_refresh_board_summaries_after_run(registry, entity_type, slug, analysis_id)`
— called from `execute_and_record_analysis` (repo) and
`execute_and_record_database_analysis` (database), right after a successful
run, on the same thread that just wrote the run's activity-log status.
Reuses `build_survey_results(..., board_id=...)` itself rather than a second,
parallel "compute a summary" implementation — that function already persists
whatever it computes when `board_id` is given, so this is "ask for this
board's fresh answer once, right now" rather than a new write path that could
drift out of sync with what a read actually returns. For repo, one run can
touch several dashboards (a dashboard's `analysis_ids` can span more than one
analysis) — every dashboard containing the just-run `analysis_id` is
refreshed, not just a same-named one.

Both the persist call and the refresh wrapper are `try/except`-wrapped and log
a warning rather than raise: a summary that fails to write is never worse than
one that was never written, and must not turn a successful run into a
reported failure.

**Filesystem has no per-analysis run route yet** (only a full-survey route) —
so no writer hook was added there in this change; filesystem board reads
always fall back to the expensive path and self-heal on first read, same as
before a writer existed for it, until a per-analysis run route exists.
Flagged as an open, minor gap below — it was out of scope for the gate, which
is entirely database boards.

## Design requirement added mid-implementation: 30-second give-up safety net

The project owner's own live gate check reported 5+ minutes to settle even
with the read-cost fix landing, as an *interim* concern in case the fix isn't
fully effective for every board on the first pass. Requirement: a card that
has not settled after 30 seconds must stop spinning and show a manual
"open to load" trigger, regardless of the board_summary fix's effectiveness
for that particular board.

Implemented in `resource_explorer/web/static/next/app.js`:

- `BY_ANALYSIS_BOARD_TIMEOUT_MS = 30000` — a `setTimeout` armed per board
  inside the progressive-fetch `worker` loop. If it fires before the fetch
  settles, `boardState` moves to a new `'timeout'` status and the card/contents-
  row render "still reading — open to load" instead of a spinner. The
  underlying fetch is **not aborted** (no `AbortController`) — same reasoning
  `live()` already applies to an abandoned stage's in-flight reads: the
  request keeps running server-side regardless, only the UI's *wait* for it
  ends. An `already` guard variable stops a late-firing timer from clobbering
  a result that lands just after, and stops a late-arriving result from being
  discarded either — whichever settles LAST wins, and both are equally
  correct answers for the same `board_id`.
- A new `retryBoard(boardId)` function — an independent fetch for exactly one
  board, outside the bounded concurrency queue (so it isn't waiting on a
  worker slot an abandoned read may still be holding), bound to the card's
  "open to load" button (`data-retry-board`) and armed with its own 30s
  give-up timer.
- `renderByAnalysisContents`'s "still reading N of M" count already treats
  anything whose status is not `'loading'` as settled, so a timed-out card
  correctly stops counting against that total the moment it gives up, rather
  than appearing to hang the whole pane.

This is a genuine safety net, not a substitute for the read-cost fix: on a
warm board_summary the fetch now resolves in ~3ms (measured below), far under
30s, so the timeout path is not expected to fire in the steady state — it
exists for the cold-path/first-run and any future regression.

## Base branch

`board_id`-scoped `build_survey_results`, `list_survey_result_boards`, and the
whole progressive-fetch pane this change's read path serves did not exist on
`origin/main` when this task started — they ship in PR #346
(`re/by-analysis-progressive-and-graph`), which was still unmerged. Since this
task's entire mechanism (the fast path, the stage-filter reorder, the
`board_id` writer hook) is meaningless without that code, this branch was
rebuilt starting from `origin/re/by-analysis-progressive-and-graph` (one
commit behind `origin/main` — a tailwind-CSS-only rebuild commit) merged with
`origin/main` to also pick up #345 (engine-note-persist). **Flagged as a
judgment call**, not a silent deviation: the PR/CI session will need to land
#346 before or alongside this branch, or this branch will need rebasing onto
`main` once #346 merges. No functional conflict was found merging the two —
`app.js` merged cleanly with one straightforward auto-merge.

## Test results

New suite: `tests/test_board_summary_read_cost.py` — 15 tests, all passing.
Covers: registry round-trip (write/read/upsert/no-collision-across-boards),
the fast path (first call recomputes+persists, second call does NOT
re-invoke the reader — asserted via `MagicMock.call_count`, not wall-clock
timing, per the design ask that this be a source-of-truth mechanism test),
missing-summary fallback, stale-summary fallback (a newer recorded run than
`source_run_at` forces recompute), a summary with no `source_run_at` treated
as always-fresh, `board_id`-less calls never engaging the fast path, the
stage-filter-before-`_read_analyses` reorder (reader not called when stage
doesn't match, is called when it does), the writer hook persisting a summary
on a successful `execute_and_record_database_analysis` run and NOT persisting
one on a failed run, and the refresh helper never raising on a broken lookup.

Extended `tests/test_next_by_analysis_progressive_and_graph.py` with an 11-test
`TestGiveUpAfter30SecondsSafetyNet` class (static source-text assertions, the
same pattern every other `/next` test in this file uses — no jsdom/DOM harness
exists in this codebase) covering: the 30s constant, the worker arming/
clearing the give-up timer around both the success and failure branches, no
`AbortController` (the fetch is intentionally not cancelled), `boardStateKey`
treating `'timeout'` like `'loading'` rather than `'error'`, the card and
contents-row both rendering "open to load" copy, the `retryBoard` function
existing and being bound to the trigger with its own timer, and the settled-
count excluding timed-out cards from "still reading".

Full suite: **`uv run pytest tests/ -q -rf`** — see below (run in the
background while writing this doc; final tally added once it completed).

## Real-data timing verification

Reached the real `laz_local_adventureworks` registry directly (shared
Postgres at `localhost:5442`, `egeria_advisor` database — default config,
credentials confirmed from `config.py`'s documented `PGVECTOR_USER`/
`PGVECTOR_PASSWORD` defaults, no `.env` needed in a Trellis checkout per this
package's own `CLAUDE.md`). Measured `build_survey_results(board_id=...)`
directly, cold (board_summary row deleted first, so this measures the same
recompute path the OLD code always paid) vs warm (second call, board_summary
row now persisted by the first call):

| Board | Cold (before-fix-equivalent) | Warm (after fix) |
|---|---|---|
| `db_classification` | 14.37s | 0.003s |
| `db_relationship_graph` | 12.96s | 0.003s |
| `db_fingerprint` | 14.59s | 0.004s |

Both gates cleared with wide margin: **per-board fetch well under 1s** (0.003–
0.004s vs the 1s bar), and a full 7-board pane settle — even every board cold
on the very first run of the day — is bounded by the writer's own persist step
at run completion, not by the pane's read; every subsequent pane load for the
same boards is ~7 × 3ms ≈ 21ms total, far under the 5s full-settle bar.

(Cold-path absolute numbers here are lower than the original gate-check
measurement's 25.8s for `db_classification` — plausible run-to-run/host-load
variance on a shared dev registry, not a discrepancy in the fix; either way
they land in the same "many seconds, not milliseconds" regime the fix exists
to eliminate from the read path.)

This was a real, additive write to the shared dev registry's new
`board_summary` table (three rows, for the three boards measured) — consistent
with the project owner's 2026-09-13 ruling that writing to the dev platform is
an ordinary shared write, not one to defer. No existing data was modified or
deleted beyond the three board_summary rows this measurement itself created
(a `DELETE ... WHERE entity_type=... AND slug=... AND board_id=...` scoped to
exactly those three rows, run immediately before each cold measurement).

## Open gaps / judgment calls flagged

1. **Base branch choice** (see above) — this branch depends on unmerged PR
   #346; needs coordination with the PR/CI session on merge order.
2. **Filesystem has no writer hook** — no per-analysis run route exists for
   filesystem yet (only full-survey), so `_refresh_board_summaries_after_run`
   is never called for `entity_type="filesystem"` today. Reads still work
   correctly (self-healing on first read), just without the "already warm at
   run completion" benefit repo/database get. Real gap only once filesystem
   grows a per-analysis run route of its own.
3. **Repo's writer hook was implemented but not independently timing-verified**
   against real data (no repo project with comparably expensive dashboard
   reads was available in this session) — covered by the mechanism tests
   (`_refresh_board_summaries_after_run`'s dashboard-membership lookup via
   `SURVEY_RESULT_DASHBOARDS`), not by a real-data measurement. The gate this
   task was scoped against is entirely database boards.
4. **The 30-second safety net's UI states were verified by static source
   assertion only**, not by driving a real browser against a genuinely slow
   board — no jsdom/DOM harness existed in this codebase at the time (the
   pattern every other `/next` test file then followed), and manufacturing a
   real 30s-plus stall against `laz_local_adventureworks` for a live click-
   through was not attempted in that session given the time budget; the
   mechanism (timer arm/clear, state transition, retry trigger) was pinned by
   source-text tests, the actual DOM rendering was not. **Closed 2026-09-28**
   — see "Harness test added" below.

## Harness test added (2026-09-28, closes gap 4 above)

PR #346 landed a real node+jsdom render harness for `/next`
(`frontend-build/test-harness/`, `NEXT-RENDER-HARNESS-IMPLEMENTED.md`)
in the meantime, with a project-owner rule (2026-09-28): every `/next` fix
from here on adds its regression to the harness, not only a source-text
test. Design flagged this branch's own gap 4 above as exactly the case that
rule exists for — a timing-dependent UI state transition only a rendered
page shows.

`frontend-build/test-harness/by-analysis-board-timeout.test.mjs` drives the
real, unmodified `loadByAnalysisPane()` (now exported — no logic change,
`export` keyword only, same pattern as this harness's other three exports)
with a stubbed `fetch`: one board's `getSurveyDashboards(..., { boardId })`
read returns a promise that never settles. Using `node:test`'s built-in
`t.mock.timers` to advance past `BY_ANALYSIS_BOARD_TIMEOUT_MS` without a real
30-second wait, it asserts the card and the contents row both render "still
reading — open to load", then dispatches a real DOM click on the rendered
`[data-retry-board]` button (not calling `retryBoard()` directly — it is a
closure private to `loadByAnalysisPane`, not a module-level export) and
asserts that click re-triggers a second `fetch()` call for the same board,
which this time resolves, taking the card out of the timeout state.
`BY_ANALYSIS_BOARD_TIMEOUT_MS` itself was also exported so the test reads the
real constant rather than hardcoding `30000`.

**Red/green verification**: with `app.js` swapped for its pre-timeout-feature
version (`4e7183f0^`, before this branch's single commit added the whole
mechanism — confirmed by `git show 4e7183f0^:...app.js | grep
BY_ANALYSIS_BOARD_TIMEOUT_MS` returning nothing), the new test fails exactly
as expected: the card stays on `reading…`/`◔` forever after the mock-timer
advance, because there is no timeout logic to transition it, and the
assertion for the "still reading — open to load" copy fails with the actual
(never-transitioning) card HTML in the diff. Restoring the real `app.js`
turns it green again. This confirms the test exercises the real timeout/
retry mechanism rather than being a tautology.

**Harness suite**: `npm run test:harness` (node v20.11.0 via nvm — this
harness needs `node --test`, unavailable on this checkout's default v14) —
8/8 pass (the pre-existing 7 plus this one; `npm install` was needed first
to bring in `jsdom`, not yet present in a fresh `frontend-build/`
`node_modules`).

## Owner gate result, adventureworks (2026-09-29): mechanism proven, one target UNMET for a reason outside this diff

Gate served on 8813, tip 7289dd88. Results by item:

1. Board visible: **PASS** (53ms).
2. All row headlines present within 2s: **UNMET**. Measured 4.4s from click to every headline present.
3. Settled within 5s: **PASS**, narrowly (4.4s).

**Root cause of item 2, confirmed not to be this branch's mechanism.** Timing `list_survey_result_boards` and each board's persisted-summary read directly, outside the running server: catalog read 0.25s, each individual board 8-33ms — the `board_summary` fast path works exactly as designed and measured earlier in this doc. But the SAME reads, made as real HTTP requests against the running server, each took 1.4-3s, with roughly two completing at a time rather than all seven in parallel as `BY_ANALYSIS_MAX_CONCURRENT_READS` should allow. A control measurement nails this down further: `GET /api/auth/me` — no board work, no database read, nothing this branch touches — took 2.3s at boot on the same server. That rules out this branch's reader/persistence code as the cause; something in the server's per-request path (candidates as identified live: the registry connection path — a shared connection or a lock; the anyio threadpool; concurrent load from other sessions' test suites sharing the same registry Postgres that night) is adding roughly a second or more of overhead to every request, board-related or not.

**Decision (design session, 2026-09-29):** this UNMET target does not block this PR — it is not this diff's defect, and the mechanism this branch actually built (persist-at-run-completion, single-row read, self-healing recompute) is proven correct and fast in isolation. A new, higher-priority slice ("per-request server latency") was opened to chase the actual cause, explicitly ahead of the planned Questions-tab read-cost follow-up, since an unfixed per-request cost would make that slice's own numbers wrong too. See that slice's own IMPLEMENTED doc once it lands. The 2s headline target stays UNMET here and is expected to close there, not in a revision of this branch.

**Two smaller fixes made on this branch as a result of the same gate:**
- Pluralization bug: the Shared Names header read "1 names carried by more than one analysis" for the single-shared-name case. Fixed (`shared.size === 1 ? '' : 's'`); harness and source-text tests updated to check for the criterion phrase without asserting a hardcoded plural.
- Board-row headline truncation ("no way to see the rest", owner feedback) — NOT fixed here. Deferred to the (a)+(c) follow-up (Table Grain description + PNG export slice) as a title attribute plus wrap-on-hover, per design's call that this is a copy/UX slice, not a read-cost one.

## Backfill CLI (2026-09-29)

**The gap this closes.** The read-cost fix above is correct but lazy: `board_summary` is written at run completion and self-heals on a cold/stale read, so the FIRST real visitor to a resource's By-analysis pane after a fresh analysis run pays the full expensive recompute, in the request path, for every board that hasn't been read yet. Found live on 2026-09-29, on a coco_pharma owner gate check: the first visit measured **over 4 minutes** to settle across all 7 discovery boards (worse than the ~1:20 pre-this-branch baseline with no persistence at all, since this branch's writes/self-heal add their own overhead on top of a cold recompute — the per-request server-latency issue from the gate result above compounds this further). Design's call: add a one-shot, out-of-band way to warm `board_summary` for a resource *before* any real user visits it, so nobody ever pays this cost live. The lazy self-heal-on-miss behavior in `build_survey_results` is unchanged — this is a second, proactive way to reach the same persisted rows, not a replacement for the safety net.

**`resource-explorer backfill-board-summaries`** (`resource_explorer/cli/main.py`). Drives the exact same recompute-and-persist path a real request would (`list_survey_result_boards` for the catalog, then `build_survey_results(..., board_id=..., include_empty=True)` per board — the same function `_refresh_board_summaries_after_run` calls at run completion and the request path calls lazily), so there is no second, parallel "compute a summary" implementation to drift out of sync.

Behavior:
- A board already fresh in `board_summary` (checked via the same `_read_board_summary_if_fresh` the read path uses) is **skipped**, not recomputed — this is a warm/backfill command, not a forced full recompute. A board whose analysis has never run is still visited, the same way a real first-read self-heals: it persists an empty-but-valid summary rather than being left with no row.
- Progress is printed per board (`backfilling <slug>: board <i>/<n> (<board_id>)... done in <seconds>s`, or `already fresh, skipped`, or `FAILED after <seconds>s — <error>`), and a final summary line reports resources processed, boards backfilled/skipped/errored, and total elapsed time.
- A single board's failure does **not** abort the run — every other board and resource is still attempted, matching the fail-soft pattern the mechanism itself already uses internally (`_read_analyses`'s per-analysis-reader try/except, `_persist_board_summary`'s never-raise contract). The command exits non-zero only if at least one board actually failed, after finishing everything else.

**Scope: `database` and `filesystem` only, not `repo`.** Repo boards were never in scope for this — see "Open gaps / judgment calls flagged" item 2 above: no per-analysis run route exists for `filesystem` either (the same gap), but `filesystem`'s board reads still go through the identical `build_survey_results(..., board_id=...)` self-heal/persist path database uses, so backfilling it out-of-band works the same way; only `repo`'s dashboard-grouped (`SURVEY_RESULT_DASHBOARDS`) shape and its own writer hook were out of scope for this CLI addition, since `repo`'s per-run writer hook already exists and this command adds nothing repo doesn't already get from a real survey run. Extending this command to `repo` would need `list_survey_result_boards`'s repo branch (dashboard ids, not analysis ids) wired the same way — straightforward if wanted, just not built here since it wasn't asked for and repo already has run-completion warming.

**Usage:**

```bash
# One resource (database or filesystem slug — auto-detected against the registry)
resource-explorer backfill-board-summaries laz_local_adventureworks

# Every registered database and filesystem
resource-explorer backfill-board-summaries --all
```

**Tests:** `tests/test_cli_backfill_board_summaries.py` — backfills a resource with no summaries (every board recomputed and persisted), skips a resource with a fresh summary already in place (reader not re-invoked), continues past a per-board failure (the other board in the same resource still gets backfilled and the failing one leaves no row behind), and `--all` iterates every registered database and filesystem. Follows `test_board_summary_read_cost.py`'s own `_wire_one_analysis`-style pattern (MagicMock results_reader, call-count assertions) rather than wall-clock timing.

**Recommended run against the two gate databases, once PR #349 merges:**

```bash
resource-explorer backfill-board-summaries laz_local_adventureworks
resource-explorer backfill-board-summaries localhost_docker_coco_pharma
```

(or a single `resource-explorer backfill-board-summaries --all` covering both, plus any other registered database/filesystem, in one run) — run right after merge, before anyone opens either resource's By-analysis pane, so the coco_pharma 4-minute first-visit finding cannot recur for either gate resource.
