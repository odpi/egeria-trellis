# DDL record: closed-loop step 2a, the `feedback_routes` table

**Status:** branch `re/closed-loop-2a-gap-routing`, not deployed. This is the deployment record for one new table in
RE's shared registry. Design: `docs/design-notes/BRIEF-CLOSED-LOOP-STEP2-ROUTING.md` §5.

Nothing in this branch has run against the shared registry (localhost:5442). The statement runs only by RE's own schema
init, the first time a process running this code opens the shared registry, after the coordinator's clearance and the
owner's word for that restart. Merging is not enough.

## The exact statement

`ProjectRegistry._migrate_feedback_routes_table` in `resource_explorer/registry.py` runs this at the end of
`_init_schema`, after `_migrate_file_systems_key_tail`, in its own short transaction:

```sql
CREATE TABLE IF NOT EXISTS feedback_routes (
    item_id   TEXT NOT NULL,
    cause     TEXT NOT NULL,
    consumer  TEXT NOT NULL DEFAULT '',
    target    TEXT NOT NULL DEFAULT '',
    reason    TEXT NOT NULL DEFAULT '',
    routed_at TEXT NOT NULL,
    routed_by TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (item_id, cause)
);
```

Same statement on SQLite and Postgres. In the `resource_explorer` schema, like every other registry table.

### What it holds

One row per diagnosis that was routed: a trellis-feedback answer item's id, the cause, the consumer (`gap`, `none`, or
`pending` while a Save is routing it), the gap id it made, and when nothing was routed, why. `routed_by` is the admin
who saved the diagnosis. No question text, no answer text and no asker id.

The primary key is what makes a Save idempotent: a route is claimed with
`INSERT ... ON CONFLICT (item_id, cause) DO NOTHING` before any work, so a second Save of the same cause, or two at
once, cannot route it twice.

### What it touches

Nothing existing. No column is added to, dropped from or retyped in any table. `item_id` is not a foreign key: the
items live in trellis-feedback's own tables.

### Lock behaviour

`CREATE TABLE IF NOT EXISTS` takes no lock on any existing table. A second init is a no-op.

### Rollback

`DROP TABLE feedback_routes;`. The gaps it made stay, and they are ordinary `analysis_gaps` rows. Their evidence names
the feedback items that raised them, which is all a later reader needs.
