# Database change comparators on the local delivery path: implemented

**Replies to:** `COORDINATOR-BRIEF-MULTI-RESOURCE.md`, Phase 1 slice #14 —
*"Database change comparators on the local delivery path (design §9.1)."*
Gate: slices 3 (structured tables, PR #180) and 9 (`db_derived`, PR #197) —
both merged and on `main`.

**Branch:** `re/db-change-comparators-local-delivery`.

**Tests:** 20 new tests (`tests/test_db_change_comparator.py` — 9;
`tests/test_scheduler.py`'s new `TestRunDueDbDerivedScheduling` — 3;
`tests/test_scheduler_subscriptions.py`'s new
`TestDatabaseSubscriptionDelivery` — 3; the rest are the pre-existing suites
those two files already had, re-run to confirm nothing regressed). Full
resource-explorer suite result is in "What could not be tested" below.

---

## What slice 9 already had, read directly before writing anything

`db_derived.py`'s `derive_change_rates()` (§5.4's `db_change_rates`, its own
write-up: `DB-DERIVED-STEP-IMPLEMENTED.md`) already computes exactly what
design §9.1's `row_drift` comparator needs: per-table
`rows_inserted`/`rows_updated`/`rows_deleted` deltas and per-day rates
between the two most recent survey snapshots, `size_bytes`/`row_count`
drift, and schema churn (tables added/removed) — all differenced from
`database_table_activity`/`database_tables` rows slice 3 already stores, no
new structured table. Absence discipline is already correct there:
`insufficient_history` (fewer than two snapshots) is distinct from a
measured `idle` table, which is distinct from `counters_reset` (a reset
between snapshots makes the subtraction meaningless, not zero).

Slice 9's own write-up explicitly scoped the *comparator* out: *"The change
detector (§5.3's 'how much has changed since the last survey?'): Design §9
owns it and slice 14 implements the comparators; `db_change_rates` here is
the per-table tuple-counter half, not the general detector."* That is
slice 14's job description, in slice 9's own words — and it turned out to
be accurate in a way worth stating precisely, because "the general
detector" already existed, generically, for repos.

## What the actual gap was — found by reading, not guessed

`notification_detector.py` (Discovery-tier Part 4, "Automate") is the
generic change detector behind every Automate subscription:
`scheduler._check_subscriptions` calls `detect_change(registry, slug,
analysis_id)` after every scheduled run, and an RFA is written iff it
reports `changed=True`. It is explicitly written to be generic *across
analysis kinds* — but only for kinds that persist through
`project_analysis_findings`/`project_analysis_metrics`
(`registry.upsert_finding`/`upsert_metric`).

Both of those tables are `FOREIGN KEY (project_slug) REFERENCES
projects(slug)` — repos only (`registry.py`'s `CREATE TABLE
project_analysis_findings`/`project_analysis_metrics`). Every repo
sub-surveyor calls `upsert_finding`/`upsert_metric` directly (confirmed by
grep across `surveyors/sub_surveyors/`). **No database step calls either
one, anywhere in the codebase.** `db_derived.py`'s six checks return
annotations for Egeria publishing; the Questions tab reads them straight
from the structured tables. Nothing ever writes a database result into
`project_analysis_findings`/`project_analysis_metrics` — nor could it,
without violating the FK, since a database slug is not a `projects` row.

The consequence, traced end to end rather than assumed: for `entity_type ==
"database"`, `detect_change()` calls `query_findings_history_raw(slug,
analysis_id)`, gets `[]` (there are no rows under any database slug in that
table), falls back to `query_metrics()`, gets `{}`, and returns
`ChangeResult(changed=False)` — **unconditionally, for every database
analysis, forever.** `scheduler._check_subscriptions` treats that
identically to "compared two runs, found nothing different." A database
Automate subscription is already a first-class UI concept — `automate.py`'s
`RESOURCE_ICON` map lists `database` alongside `repo`/`filesystem`, and its
subscription-create route only validates project existence for
`entity_type == "repo"` — so a user can create one today. It could never
have fired. Nothing before this said so; the local Automate UI shows
`last_checked_at`/`notification_count` for a subscription with no visible
distinction from "watched and quiet."

This is precisely "database change comparators on the local delivery
path": §9.2 (*"Delivery: local now, Egeria-integrated in three steps"*)
names the scheduler → detector → RFA chain as *"local"* delivery, and that
chain had no working comparator for the one resource type this slice owns.

## What was built

**`resource_explorer/surveyors/database/db_change_comparator.py`** — the
bridge. `detect_database_change(registry, slug, analysis_id)` is the
database-side counterpart to `notification_detector.detect_change`,
dispatched by `scheduler._check_subscriptions` when `entity_type ==
"database"` instead of the generic detector. It reads
`derive_change_rates`'s result directly — no delta is recomputed — and
translates it to a `ChangeResult`:

- **`changed=True`** when any table is labelled `active` (a nonzero rate)
  or schema churn added/removed a table. The summary names the tables and
  their deltas, reusing `derive_change_rates`'s own numbers.
- **`changed=False, established=True`** when both snapshots compared
  cleanly and nothing moved — a real measured negative.
- **`changed=False, established=False`** when `derive_change_rates` itself
  is `insufficient_history` (fewer than two snapshots) or any other
  not-measured state. Both `established` states suppress an RFA
  identically — a subscriber is never told "no change" when the honest
  answer is "cannot tell yet" *or* when it genuinely didn't change; the
  field exists so a caller reporting *why* nothing fired (a future Automate
  UI affordance, or a test) can tell the two apart, rather than because the
  scheduler's own delivery decision needs it.
- **A `counters_reset` table never counts as activity** — `derive_change_rates`
  already marks it not-measured for exactly that table, distinct from
  `active`; the comparator reads that distinction rather than treating any
  non-`idle` label as a change.

`DATABASE_CHANGE_COMPARATORS: dict[str, Callable]` maps `analysis_id` →
comparator, currently one entry (`db_change_rates`), mirroring
`db_derived.DB_DERIVED_ANALYSES`'s reasoning for a single list over
scattered `if analysis_id ==` checks — the one place to add each of §9.1's
remaining comparators as its data becomes diffable (see "Scoped out").
`detect_database_change` for any other `analysis_id` returns `changed=False,
established=False` with an explicit "no comparator implemented yet"
summary — never a bare, indistinguishable `False`.

**`notification_detector.ChangeResult` gained one field, `established:
bool = True`.** Defaulted so every pre-existing call site (all
findings/metrics-backed, and genuinely comparable once there are two
batches) is unchanged — `ChangeResult(changed=False) ==
ChangeResult(changed=False, established=True)`, so no existing test needed
to change.

**`scheduler._check_subscriptions`** now dispatches: `entity_type ==
"database"` → `detect_database_change`; everything else → the existing
generic `detect_change`. The generic detector's own docstring ("Generic
across every analysis kind ... no per-analysis-kind code here") is
unmodified and still true for repos; the resource-type split lives in the
scheduler, not inside `notification_detector.py`.

## A second, related bug found and fixed while verifying — not guessed at

Wiring the comparator up meant actually scheduling `db_change_rates` end to
end, which surfaced a real bug in `scheduler._run_db_survey`: a `db_derived`
`analysis_id` (all six of slice 9's checks, including `db_change_rates`
itself) is **not** in `DATABASE_ANALYSIS_STEP_MAP`, so
`_run_db_survey`/`_run_local_db_survey` fell through to the credentialed
survey path — which (a) refuses to run at all without stored
`db_user`/`db_password`, even though `db_derived` needs none, and (b) would
have run the **full** `DatabaseSurveyor` (`steps=None`, since the id has no
step-map entry) instead of the zero-fetch step, had credentials been
present. `web/routes/databases.py`'s per-card manual "Run" route already
special-cased `DB_DERIVED_ANALYSES` correctly (slice 9 built that one); the
scheduler's independent re-implementation of "how to run this analysis_id"
never learned the same lesson.

This is not a hypothetical edge case for this slice: it directly blocked
the very thing slice 14 exists to enable — scheduling `db_change_rates` on
a cadence for exactly the database-unreachable case `db_derived` was built
to still answer for (`DB-DERIVED-STEP-IMPLEMENTED.md`'s own framing: *"a
zero-fetch step ... unable to run at all for a database whose credentials
are missing"*). Without the fix, a database with no stored credentials
could never have its `db_change_rates` subscription checked at all, since
the *scheduled run itself* would fail with "No stored database credentials"
before the comparator ever ran. Fixed in `_run_db_survey` by checking
`DB_DERIVED_ANALYSES` and calling `run_db_derived` directly, before the
step-map/credentials path — mirroring the web route's own ordering and
comment. Covered by `test_scheduler.py`'s new
`TestRunDueDbDerivedScheduling` (credential-free success, the credentialed
path is never reached, an exception is recorded not raised).

## Absence discipline, end to end

| Layer | Not established | Measured negative |
|---|---|---|
| `derive_change_rates` (slice 9, unchanged) | `insufficient_history` — fewer than two snapshots | a table `idle` — two snapshots, no rows moved |
| `db_change_comparator` (this slice) | `ChangeResult(changed=False, established=False)` | `ChangeResult(changed=False, established=True)` |
| an `analysis_id` with no comparator at all | `ChangeResult(changed=False, established=False)`, summary names the missing comparator | *(not applicable — nothing was measured)* |
| the RFA delivery decision (scheduler, unchanged) | no RFA | no RFA |

The delivery decision is deliberately the same in both `established` states
— a subscriber should not be notified either way — but the distinction is
now carried through instead of collapsing at the first layer that doesn't
need it, matching the brief's absence-discipline framing (a chart or a
notification path silently treating "not enough history" as "nothing
changed" is the named failure mode this whole multi-resource plan keeps
finding and re-fixing).

## Scoped out

- **The other six comparators in design §9.1's table**: `schema_diff`
  (beyond table add/drop — column/constraint-level diffing), `grant_change`,
  `class_change`, `reference_set_change`, `scope_change`,
  `resilience_change`. Each needs a two-snapshot diff over data this
  codebase already collects elsewhere (`postgres_operations`'s
  `privilege_audit`/`db_resilience` — slice 8; `data_class_match`/
  `reference_data_match` — slice 10; proposed `DataScope` — slice 9), but
  none of those checks is differenced across runs today; building each is a
  comparator apiece, not a rerun of this bridge. `DATABASE_CHANGE_COMPARATORS`
  is the one place to add them. Logged to `docs/Backlog.md`.
- **A UI affordance distinguishing `established=False` from a real
  measured-quiet subscription.** The Automate subscriptions table shows
  `last_checked_at`/`notification_count`, neither of which currently
  surfaces *why* nothing fired. Worth doing once a second comparator exists
  to make the contrast visible; one comparator alone doesn't yet justify the
  UI work. Logged.
- **The pre-existing version of this same absence gap on the repo side.**
  `notification_detector._detect_findings_change`/`_detect_metrics_change`
  both return `ChangeResult(changed=False)`, not `established=False`, for a
  kind with fewer than two history batches — the same "insufficient
  history vs. measured negative" conflation this slice fixed for databases,
  present in the repo path since before this slice and not introduced by
  it. `established` would apply there unchanged; left as found, since
  fixing repo-side behavior is outside a database-only slice. Logged.
- **Understanding-tier charts over `db_change_rates`'s per-table series.**
  Confirmed still not built — genuinely different work from the local
  delivery path (design §9.2 vs. §11's chart representations), not
  something this slice's "local delivery path" framing was ever about
  (§9.2 is explicitly titled "Delivery," and the coordinator brief's row
  for slice 14 quotes that exact phrase). Slice 9's write-up already logged
  this scope item; it stays open.

## What could not be tested

- **Against a live scheduled run over real Postgres activity.** Every test
  drives `derive_change_rates`/`detect_database_change`/the scheduler
  through hand-built structured-table rows, the same pattern
  `test_db_derived_step.py` uses — not through rows an actual `coco_ods`
  survey produced across two real runs.
- **The Automate UI actually surfacing `established=False`.** No UI
  affordance was built (see "Scoped out"); the field exists in the Python
  layer only, verified by unit and scheduler-level tests, not by driving
  the browser.
- **Full test suite.** See the PR for the exact pass/fail counts from this
  run; `uv run pytest tests/ -v` was executed in full before finalizing per
  the slice's constraints.
