# Outbox drain race — investigated, found already fixed in code, docstring/backlog were stale

**2026-09-19.** Assigned as a Tier 1 bug: `ProjectRegistry.claim_due_outbox_elements()`
(`resource_explorer/registry.py`) was reported as a plain `SELECT` with no
`FOR UPDATE`/`SKIP LOCKED` and no status transition, so two concurrent
drainers — a person running a manual republish, and `scheduler.py`'s own
loop, which fires every `_CHECK_INTERVAL_SECONDS` (900s) regardless of
whether anyone is at the keyboard — could both select the same due rows and
both call `apply_element` on them. `docs/Backlog.md`'s entry ("The outbox
drain does not serialise, and its docstring says it does") named the fix:
`SELECT ... FOR UPDATE SKIP LOCKED`, or a `status='running'` transition in
the same transaction as the select.

## What was actually found

The fix already exists in `registry.py`, committed 2026-09-02
(`472f83c5d`) — a few hours after `be838376e` corrected the docstring to
document the bug as unsolved. Nobody revisited that docstring once the real
fix landed, so it kept asserting "despite the name, it does not claim" while
the code beneath it did claim. The backlog entry was never marked fixed
either. Both are stale documentation of a bug that no longer exists — the
same failure shape the entry itself calls out: *"a function named `claim_`
that performs no claim, documented as doing the locking it does not do, is
worse than an undocumented race."* A docstring asserting the code does
nothing, when it actually does the right thing, is the same trap in the
other direction: a reader who trusts the docstring over the code will
"fix" a bug that isn't there, or worse, remove the real fix while
"restoring" the documented (non-)behaviour.

Regression tests for the fix already existed too
(`tests/test_egeria_outbox.py::TestTheClaimActuallyClaims` and
`TestTheClaimSqlIsValidOnPostgres`), written in the same 2026-09-02 pass.

So this pass's actual work was: verify the fix is real and complete against
this task's four requirements, correct the two stale artifacts (the
docstring, the backlog entry), and confirm the existing tests genuinely
cover what they claim to.

## The fix, as implemented

`claim_due_outbox_elements()` selects and transitions status in one
transaction:

```python
with self._conn() as conn:
    if conn.is_postgres:
        sql += " FOR UPDATE SKIP LOCKED"
    ids = [r["id"] for r in conn.execute(sql, tuple(params)).fetchall()]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE egeria_outbox SET status='running', claimed_at=? "
        f"WHERE id IN ({marks})",
        tuple([now, *ids]),
    )
    rows = conn.execute(
        f"SELECT * FROM egeria_outbox WHERE id IN ({marks}) ORDER BY id ASC",
        tuple(ids),
    ).fetchall()
```

The due-row predicate itself widened to include rows already `running` whose
lease has expired:

```sql
WHERE (o.status IN ('pending', 'failed')
       OR (o.status = 'running' AND o.claimed_at <= ?))   -- lease_cutoff
  AND o.next_attempt_at <= ?
  AND (o.depends_on_id IS NULL OR EXISTS (
        SELECT 1 FROM egeria_outbox dep
        WHERE dep.id = o.depends_on_id AND dep.status = 'done'))
```

## Why `status='running'` + `FOR UPDATE SKIP LOCKED`, not one alone

The task brief asked which of the two backlog-named options fits better, and
whether the codebase's existing advisory-lock pattern (`worker.py` /
`leader_election.py`) is more idiomatic here. Both were real options; the
implementation combines the two rather than picking one, and that
combination is the right call for this specific shape of problem:

- **A bare `status='running'` transition alone** (no `FOR UPDATE`) is not
  actually atomic across two backends running the same statement
  concurrently unless the database's isolation level makes read-then-write
  safe by itself. On SQLite, this is true almost by accident: SQLite
  serialises writers at the file/connection level, so two `UPDATE`s racing
  for the same rows queue up rather than interleave. On Postgres under the
  default `READ COMMITTED` isolation, a naive `SELECT` (to find candidate
  rows) followed by an `UPDATE` (to claim them) is NOT safe: two
  transactions can both run the `SELECT` before either commits its
  `UPDATE`, and both see the same "pending" rows. Postgres's row-level
  locking has to be invoked explicitly for this shape (a queue of N workers
  competing for a bounded batch of due rows) — `SELECT ... FOR UPDATE` is
  exactly the primitive for "lock the rows I'm about to act on, for the
  duration of my transaction."

- **`FOR UPDATE` alone (no `SKIP LOCKED`)** would make the second drainer's
  `SELECT` *block* until the first drainer's transaction commits or rolls
  back, then see the now-committed row's new status and (correctly) not
  reclaim it — safe, but serialises the two drains end-to-end for no
  reason: the second drainer wants disjoint work, not to wait its turn on
  work the first is already doing. `SKIP LOCKED` makes the second drainer
  skip past rows the first has locked and take whatever else is due
  instead, which is the actual goal ("N workers competing for a queue of
  due rows" — the standard queue-worker pattern `SKIP LOCKED` exists for).

- **Postgres advisory locks** (the `worker.py`/`leader_election.py`
  pattern elsewhere in this codebase) are the right tool for "exactly one
  of N processes may do this specific thing" — leader election, a
  singleton background job. They are the wrong tool here because the goal
  is the opposite: **many drainers should all make progress at once**,
  each on a disjoint slice of the queue. An advisory lock around the whole
  claim would turn concurrent drains into serialized ones (the second
  drainer waits for the first's entire claim-batch, not just the rows it
  wants), with no compensating benefit over `SKIP LOCKED` — and would still
  need a `status='running'` transition anyway for the SQLite fallback,
  since SQLite has no advisory-lock equivalent.

- **Why `status='running'` still matters even with `FOR UPDATE SKIP
  LOCKED`:** the row lock only lasts the transaction. Once the claiming
  transaction commits, the lock is gone — a subsequent claim call (the
  *same* drainer's next pass, or another one) needs a durable signal that
  this row is already being worked, which is what `status='running'` +
  `claimed_at` provides. It is also what makes SQLite's fallback correct:
  SQLite gets no `FOR UPDATE SKIP LOCKED` (that clause is only appended
  `if conn.is_postgres`), but the single-transaction select-then-update,
  combined with SQLite's own writer serialisation, is sufficient there —
  the second transaction simply cannot start its own claim attempt until
  the first's transaction (select + update) has fully committed, and once
  it does, the rows it claimed already read `status='running'`.

So the two backlog-named options were not actually alternatives to choose
between for this codebase — `status='running'` is required unconditionally
(it is what SQLite gets, and what gives Postgres claims their durable
"already taken" signal across transactions), and `FOR UPDATE SKIP LOCKED` is
the additional, Postgres-only piece needed to make the same-transaction
select-and-update itself race-free under `READ COMMITTED`, without
serialising unrelated drainers against each other.

## Failure handling (requirement 2: a claimed-but-failed row must not be stuck)

Three distinct release paths exist, matching three distinct ways a claim can
go wrong, all already present:

1. **The create against Egeria raises.** `drain_outbox()`'s per-row
   `except Exception` branch calls `registry.mark_outbox_failed(...)`,
   which sets `claimed_at=''` and moves status to `failed` (with
   exponential backoff via `next_attempt_at`) or `dead` past the attempt
   cap — never leaves the row in `running`.
2. **No Egeria client is reachable at all** (the whole batch, not one row).
   `drain_outbox()`'s no-client branch calls
   `registry.release_outbox_claim([...])` on every row it just claimed,
   which resets them to `status='pending'` with `attempts`/`next_attempt_at`
   untouched — an outage does not burn a retry attempt, distinct from a
   real failed create.
3. **The drainer process dies mid-claim** (no code path runs at all — a
   `pkill`, a crash). Nothing can react to this by definition, so it is
   handled structurally instead: `CLAIM_LEASE_SECONDS` (1800s) bounds how
   long a `running` row is excluded from the due-row predicate. Once
   `claimed_at` is older than the lease, the row becomes claimable again
   even though its status never changed. This is the case
   `test_a_killed_drainer_does_not_strand_its_rows_forever` pins, and it is
   the actual incident that motivated writing this class of test in the
   first place (2026-09-02: a batch was `pkill`'d mid-run).

A fourth case worth naming because it is easy to conflate with a failure:
`OutboxNotReadyError` (a link row whose referent annotation hasn't landed
yet) goes through `mark_outbox_deferred()`, not `mark_outbox_failed()` — back
to `pending` with **no** attempt burned, because the row isn't broken, it's
early. `TestDeferredLinksWaitForTheirReferents` covers this path
separately; it's not a race-safety concern (nothing else can claim the row
in the same window) but it shares the same "must not get stuck" property.

## Test coverage (requirement: regression tests for the race and for release-on-failure)

Already present, added in the same 2026-09-02 pass as the fix, verified
still correct and still green in this pass:

- **`tests/test_egeria_outbox.py::TestTheClaimActuallyClaims`**
  - `test_two_claimers_never_get_the_same_row` — the core regression: two
    sequential calls to `claim_due_outbox_elements()` (simulating two
    drainers) return disjoint sets; the second gets nothing once the first
    has taken everything due.
  - `test_a_claim_is_visible_as_running` — the claimed row's status is
    externally observable, not just internally consistent.
  - `test_peek_does_not_claim` — `peek_due_outbox_elements()` (added
    alongside the fix, for status panels / tests that need to look at the
    queue without taking it) never mutates state.
  - `test_a_killed_drainer_does_not_strand_its_rows_forever` — the lease
    expiry path (failure mode 3 above).
  - `test_an_outage_hands_the_claim_back_rather_than_holding_it` — the
    no-client release path (failure mode 2 above); asserts
    `outbox_counts() == {"pending": 1}`, i.e. released, not left `running`.

- **`tests/test_egeria_outbox.py::TestTheClaimSqlIsValidOnPostgres`** — this
  answers the brief's question about how a Postgres-specific-locking test
  should be marked/skipped when a real connection isn't available. This
  codebase's existing answer, already established before this task: **it
  doesn't gate a real-Postgres test behind a skip marker at all — it pins
  the SQL text itself**, because the class of bug that matters here (a SQL
  construct SQLite silently tolerates and Postgres rejects outright) is
  caught by asserting the query shape, not by executing it against a live
  server. The class's own docstring documents why this was necessary: an
  earlier version of the claim combined `FOR UPDATE` with a `LEFT JOIN` for
  the dependency check; every test in this file passed, because they all
  run on SQLite, which enforces neither; the real bug (`FeatureNotSupported:
  FOR UPDATE cannot be applied to the nullable side of an outer join`) only
  surfaced against the real backend, via unrelated Postgres-backed route
  tests failing in the full suite. So the dependency check was rewritten as
  a correlated `EXISTS` specifically to be compatible with `FOR UPDATE`, and
  `test_the_claim_does_not_combine_for_update_with_an_outer_join` pins that
  shape directly from `inspect.getsource()` (comments stripped, so the
  check can't be satisfied by prose describing the absence of a join
  instead of the actual absence). No `requires_postgres`-style marker or
  fixture exists anywhere else in this suite (`grep` across `tests/`
  confirms it), so there was no established pattern to follow for gating a
  real-connection test — the source-inspection approach is this codebase's
  actual answer to "how do you test a Postgres-only SQL correctness
  property without a live Postgres."

No new tests were added in this pass — the existing suite already covers
both requirements (disjoint claims under concurrency, and release-not-stuck
on failure) at the fidelity the codebase already uses for this kind of bug.
The work in this pass was auditing that coverage against the task's stated
requirements and confirming it holds, not writing it.

## RegistryConfig / SQLite vs Postgres (requirement 3)

`RegistryConfig.database_url` (`resource_explorer/config.py`) defaults to
the shared Postgres instance (`REGISTRY_DATABASE_URL` unset), with SQLite as
an explicit override for "a from-scratch environment without the shared
instance" — still a real, supported path, not a deprecated one. The fix
handles both: `FOR UPDATE SKIP LOCKED` is appended only when
`conn.is_postgres`, and the surrounding single-transaction select-then-update
is what SQLite relies on instead, backstopped by SQLite's own writer
serialisation. `tests/test_egeria_outbox.py` runs entirely against the
SQLite tier (the default test fixture), which is exactly why
`TestTheClaimSqlIsValidOnPostgres` exists as a separate, source-level check
for the Postgres-only clause the SQLite run can't exercise.

## Files touched in this pass

- `resource_explorer/registry.py` — corrected `claim_due_outbox_elements()`'s
  docstring, which asserted the pre-fix (2026-09-02, `be838376e`) behaviour
  and was never updated once the fix (`472f83c5d`, same day) landed.
- `docs/Backlog.md` — marked the entry FIXED with this note's date, kept the
  original entry's reasoning about the asymmetric hazard (annotations
  survive a double-apply, annotation links do not) since that reasoning is
  still why the fix mattered.
- `OUTBOX-DRAIN-RACE-FIXED.md` — this file.
