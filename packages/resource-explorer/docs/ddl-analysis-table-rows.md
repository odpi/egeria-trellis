# DDL record: `analysis_table_rows` (claude/re-b1-table-result)

**Status:** branch `claude/re-b1-table-result`, not deployed. This is the deployment record for one new table in RE's
shared registry: the stored rows of a table result (B1, DESIGN-B1-MEMBER-SCOPED-TABLE-RESULT-2026-11-02 §4).

**Decision (project owner, 2026-11-02):** "Yes, B1 next"; show 1,000 rows and store all of them; send only the summary to
Egeria. The table holds every row; nothing in it is published.

Nothing in this branch has run against the shared registry (localhost:5442). The table is created only by RE's own
schema init, the first time a process running this code opens the shared registry, and only after the owner's word
for that restart. Merging is not enough.

## The exact Postgres DDL

`ANALYSIS_TABLE_ROWS_DDL` in `resource_explorer/registry.py`, run by `_init_schema` right after `STEP_ANNOTATIONS_DDL`:

```sql
CREATE TABLE IF NOT EXISTS analysis_table_rows (
        run_id         TEXT NOT NULL,
        analysis_id    TEXT NOT NULL,
        resource_kind  TEXT NOT NULL,
        slug           TEXT NOT NULL,
        row_no         INTEGER NOT NULL,
        row_key        TEXT NOT NULL,
        row_json       JSONB NOT NULL,
        PRIMARY KEY (resource_kind, slug, analysis_id, run_id, row_no)
    );
CREATE INDEX IF NOT EXISTS idx_analysis_table_rows_member ON analysis_table_rows(analysis_id, slug, row_key);
CREATE INDEX IF NOT EXISTS idx_analysis_table_rows_run ON analysis_table_rows(run_id);
```

The table is unqualified, so it lands in the connection's `search_path` schema: `resource_explorer` for RE's registry
URL. The block is additive and idempotent: `IF NOT EXISTS` only, no `ALTER` of an existing table, no data migration, no
foreign keys. Every later `_init_schema` run is a no-op. SQLite accepts the `JSONB` type name as a column of no
particular affinity; the writer stores a JSON string either way, and the reader decodes a string (SQLite) or takes the
decoded object psycopg2 returns for `JSONB` (Postgres).

**One change from the brief's key.** The brief's `PRIMARY KEY (run_id, row_no)` would collide: `run_id` is the run's
own time, the executor's `surveyed_at`, and one survey gives every step it runs the same `surveyed_at`. Two table
results of one survey (value_profile and, later, the quality check rows) would then share `(run_id, row_no)`. The key
carries the resource and the analysis as well, and `run_id` keeps an index of its own for the "rows of this run" read.

### What the columns are

| Column | Holds |
|---|---|
| `run_id` | the run's own time, the executor's `surveyed_at`: the link to the step's `step_runs` row |
| `analysis_id` | the catalogue id of the analysis whose declaration is the table result (`value_profile`) |
| `resource_kind` | the resource type (`database`, `filesystem`) |
| `slug` | the resource's normalized slug |
| `row_no` | the row's place in the declared order, from 1 |
| `row_key` | the declared key fields' values as a JSON array (`["public","orders","id"]`) |
| `row_json` | the row, one field per declared column; an empty cell is NULL, worded by the reader, never 0 |

Rows are written by `result_materializer.write_table_rows`, which refuses a row with an undeclared field, without a
key, or with a state its column does not list; and read by `table_result.table_rows`, which applies the declaration.
A re-run of the same run replaces that run's rows.

## Which processes would create it

The same as `ddl-step-annotations.md`: 8813 on its restart after `wt-relgraph` moves to a commit carrying this change
(the intended creator); 8810 if the main checkout is fast-forwarded past the merge (`--reload` restarts on the file
change); the Prefect worker on its next start; any CLI or probe that constructs `ProjectRegistry` from a checkout
carrying the change. Test runs create it in their own throwaway schema.

## BEFORE and AFTER

No snapshot script is written for this table yet. The step_annotations snapshot
(`~/.re-gate/scripts/ddl_step_annotations_snapshot.py`) is the template: check the table is absent before; after,
that it exists once in `resource_explorer`, its columns equal the DDL's in order, both indexes and the primary key are
present, and every other count is unchanged apart from ordinary traffic.
