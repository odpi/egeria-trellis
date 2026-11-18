# DDL record: TR-21, the `flow_run_results` table

**Status:** branch `re/prefect-no-local-fallback`, not deployed. This records one new table in RE's shared registry.
Design: the Architect's ruling of 2026-11-15, TR-20 and TR-21 (RULING-PREFECT-NO-LOCAL-FALLBACK-AND-RESULT-TRANSPORT,
refined at 17:30Z), which is held with the gate records on laz, not in this repo.

Nothing in this branch has run against a shared registry. Deploying it needs the project owner's word for that host,
plus the coordinator's DDL clearance. Merging is not enough. Hedwig goes first. Laz goes before its container worker
starts, after its own peer round.

## Why

A dispatched step's flow run returned its output as a Prefect result file. The worker wrote that file to its own
filesystem, and RE read it back from that path. Laz's host worker shares RE's filesystem, so this worked there. A
container worker does not share it. On hedwig, 2026-11-15, the read failed with "outside of the base path", and RE ran
the step a second time locally.

TR-21 makes the registry the result. The flow writes its output here, keyed by its flow run id, before it returns. RE
reads it by that id and by nothing else. The flow no longer persists a result file (`persist_result=False`).

## The exact statement

Apply it on its own first, so that no full schema init runs (about 150 statements, some taking ACCESS EXCLUSIVE):

```sql
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '30s';
SET LOCAL search_path = resource_explorer;
CREATE TABLE IF NOT EXISTS flow_run_results (
    flow_run_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    slug        TEXT NOT NULL,
    step_key    TEXT NOT NULL,
    step_run_id INTEGER,
    output      TEXT NOT NULL,
    norm_hash   TEXT NOT NULL,
    written_at  TEXT NOT NULL
);
COMMIT;
```

On hedwig, from the checkout:

```bash
docker exec -i egeria-shared-postgres psql -h localhost -p 5442 -U egeria_advisor -d egeria_advisor \
  -v ON_ERROR_STOP=1 < packages/resource-explorer/docs/ddl-flow-run-results.sql
```

`ProjectRegistry._migrate_flow_run_results_table` runs the same `CREATE TABLE IF NOT EXISTS` at the end of
`_init_schema`, after `_migrate_feedback_rfas_table`. On a registry where the table already exists, that is a no-op.
SQLite and Postgres run the same statement.

`step_runs` needs no change. Its `flow_run_id` column has existed since 2026-09-28 (Prefect dispatch honesty).

### What it holds

Each row is one Prefect flow run of `RE Survey Flow`:

- `flow_run_id`: the key, Prefect's id for the run.
- `entity_type`, `slug`, `step_key`: what the step ran on.
- `step_run_id`: the run's one work row in `step_runs`.
- `output`: the step's output dict, as JSON text. It is scrubbed of any one-run credential by
  `run_surveyor_step_task`, under the same rules as the kept survey snapshot.
- `norm_hash`: sha256 of that canonical JSON.
- `written_at`.

`output` is TEXT holding JSON, not `jsonb`. That matches every other JSON column in the registry: one DDL serves both
backends (see the `step_runs` note in `registry.py`).

Writes are `INSERT ... ON CONFLICT (flow_run_id) DO NOTHING`. A second write for the same flow run never overwrites,
and if it carries a different `norm_hash` it is logged as an error.

### What it touches

No existing table is altered. The scheduler's housekeeping pass prunes rows older than 30 days
(`FLOW_RUN_RESULTS_KEEP_DAYS`, the Architect's default; change it only on the project owner's word), next to the
outbox retention purge.

### Lock behaviour

`CREATE TABLE IF NOT EXISTS` takes no lock on any existing table. It is bounded by the 10 s lock timeout and the 30 s
statement timeout above. A second run is a no-op.

### Rollback

Run `DROP TABLE flow_run_results;`, then deploy the previous code. Without the table, code with TR-21 cannot keep a
dispatched step's output: the flow fails, and RE records the run as failed with no local re-run (TR-20). Steps that
are not dispatched to a worker are unaffected.
