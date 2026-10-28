# DDL record: `step_annotations` (re/analytics-2c-database-staleness)

**Status:** live. Deployment record for the step_annotations table (2026-10-13).

The deployment record for one new table in RE's shared registry, following the template the architect
endorsed on 2026-10-08 for `resource_scope_events`. **Decision (project owner, 2026-10-13):** user-step
annotations get a dedicated table instead of reusing `native_survey_annotations`, which holds Egeria's
read-back only.

Nothing in this branch has run against the shared registry (localhost:5442). The table is created only by RE's
own schema init, the first time a process running this code opens the shared registry, and only after the
owner's explicit word for that restart. Merging is not enough.

## The exact Postgres DDL

`STEP_ANNOTATIONS_DDL` in `resource_explorer/registry.py`, run by `_init_schema` right after
`RESOURCE_SCOPE_EVENTS_DDL`. This is the text Postgres receives after RE's translator
(`PostgresCursorWrapper._translate_sql`; `AUTOINCREMENT` becomes `SERIAL`):

```sql
CREATE TABLE IF NOT EXISTS step_annotations (
        id               SERIAL PRIMARY KEY,
        entity_type      TEXT NOT NULL,
        slug             TEXT NOT NULL,
        step_key         TEXT NOT NULL,
        package          TEXT NOT NULL DEFAULT '',
        producer_version TEXT NOT NULL DEFAULT '',
        run_id           TEXT NOT NULL,
        annotation_type  TEXT NOT NULL DEFAULT '',
        scope            TEXT NOT NULL,
        result_state     TEXT NOT NULL DEFAULT '',
        summary          TEXT NOT NULL DEFAULT '',
        explanation      TEXT NOT NULL DEFAULT '',
        measured_at      TEXT NOT NULL DEFAULT '',
        detail_json      TEXT NOT NULL DEFAULT '{}',
        created_at       TEXT NOT NULL,
        UNIQUE (entity_type, slug, step_key, run_id, scope)
    );
CREATE INDEX IF NOT EXISTS idx_step_annotations_latest ON step_annotations(entity_type, slug, step_key, measured_at);
CREATE INDEX IF NOT EXISTS idx_step_annotations_state ON step_annotations(entity_type, slug, step_key, result_state);
```

The table is unqualified, so it lands in the connection's `search_path` schema: `resource_explorer` for RE's
registry URL. The block is additive and idempotent: `IF NOT EXISTS` only, no `ALTER` of an existing table, no
data migration, no foreign keys. Every later `_init_schema` run is a no-op.

### What the columns are

| Column | Holds |
|---|---|
| `step_key` | the step's key (`database_staleness`) |
| `package`, `producer_version` | the step's package and version (envelope `producerVersion`) |
| `run_id` | the run's own time, the executor's `surveyed_at`: the link to the step's `step_runs` row |
| `annotation_type` | the annotation type (`ResourceMeasureAnnotation`) |
| `scope` | what the annotation is about (e.g. `public.orders`) |
| `result_state` | the envelope's state, as a column so readers can filter on it |
| `detail_json` | `{scope, resourceProperties, additionalProperties}`, with the whole envelope in `additionalProperties` |
| `created_at` | when RE stored the row |

The `step_runs` link is the tuple (entity_type, slug, step_key, surveyed_at = run_id). The `step_runs` row is
written after the step returns, so no row id exists to store when the annotations are written. A test joins
the two tables through this tuple.

The unique key (entity_type, slug, step_key, run_id, scope) makes a re-run of the same run write nothing new.

## Which processes would create it

The table is created the first time ANY process running this code constructs a `ProjectRegistry` against the
shared registry. Every such process:

1. **8813**, the gate server (`.claude/worktrees/wt-relgraph`, detached at the tip), on its restart after that
   worktree is moved to a commit carrying this change. This is the intended creator.
2. **8810**, the main checkout's `resource-explorer web --reload`. **Note:** `--reload` restarts the app on a
   file change, so fast-forwarding `/Users/dwolfson/localGit/egeria-v6/trellis` past the merge creates the
   table with no explicit restart. Fast-forward the main checkout only after the owner's word, or after 8813
   has already created the table.
3. **The Prefect worker** (`uv run --package resource-explorer`, a bare host process on the main checkout), the
   next time it starts on the new code, or the next flow run that constructs a registry in a fresh process.
4. **Any CLI** run from a checkout carrying the change (`resource-explorer ...`, `uv run python -m
   resource_explorer...`, `scripts/*.py` that construct `ProjectRegistry`), including read-only probes through
   `~/.re-gate/scripts/re-py.sh` from a branch worktree. Constructing `ProjectRegistry` runs migrations; see the
   memory note "ProjectRegistry() runs migrations".
5. **Test runs that reach 5442.** The pg tier (`requires_pgvector`, without `PGVECTOR_PORT=1`) creates the table
   in its own throwaway test schema through `pg_test_schema`, not in `resource_explorer`. A test that
   constructs a default `ProjectRegistry()` with no `REGISTRY_DATABASE_URL` would reach the shared schema; the
   registry guard refuses that. The analytics library's `OdsReader` never constructs `ProjectRegistry`.

Nothing in Egeria, the Portal or Egeria Advisor constructs RE's registry.

## BEFORE snapshot: `~/.re-gate/scripts/ddl_step_annotations_snapshot.py before`

Read-only: one psycopg2 session with `readonly=True`, SELECTs only. It never imports `resource_explorer`, so it
cannot create the table it looks for, whichever checkout it runs from. It records:

- the server time;
- the schemas;
- the base-table count in `resource_explorer`;
- whether `step_annotations` exists, in any schema (expected: absent);
- the counts of `projects`, `activity_log`, `egeria_outbox`, `app_settings`, `native_survey_annotations` and
  `step_runs`;
- the granted advisory locks, with pid, application name and backend start.

It exits 1 if the table already exists.

```
REGISTRY_SNAPSHOT_URL=postgresql://<user>:<pwd>@localhost:5442/egeria_advisor \
    python ~/.re-gate/scripts/ddl_step_annotations_snapshot.py before > before.txt
```

## AFTER check: the same script, `after`

It prints everything the BEFORE snapshot does, then checks that:

- `step_annotations` exists exactly once, in `resource_explorer`;
- its columns equal the DDL's, in order;
- both indexes are present;
- the unique constraint is `UNIQUE (entity_type, slug, step_key, run_id, scope)`;
- it has 0 rows. Rows are reported, not failed: a step may have run since creation.

It exits 1 if any check fails. Then `diff before.txt after.txt`:

- the other counts should be unchanged, apart from ordinary traffic;
- the base-table count should be up by one;
- the advisory locks should be held by new backends.

On health: the block stays ON, there are no errors, the three leaders hold their locks, and the pages return
200.

## Creator honesty

Postgres keeps no creation timestamp for a table. The record says which process ran first after the owner's
word: the 8813 restart, if the main checkout was not fast-forwarded before it. Later `_init_schema` runs from
any other process are no-ops under `IF NOT EXISTS`.
