# DDL record: closed-loop step 2b, the `feedback_rfas` table

**Status:** branch `re/closed-loop-2b-rfa-routing`, not deployed. This is the deployment record for one new table in
RE's shared registry. Design: `docs/design-notes/BRIEF-CLOSED-LOOP-STEP2-ROUTING.md` §3 ("One RFA per question").
It follows step 2a's `feedback_routes` (`docs/ddl-feedback-routes.md`).

Nothing in this branch has run against the shared registry (localhost:5442). The statement runs only by RE's own schema
init, the first time a process running this code opens the shared registry, after the coordinator's clearance and the
owner's word for that restart. Merging is not enough.

## The exact statement

`ProjectRegistry._migrate_feedback_rfas_table` in `resource_explorer/registry.py` runs this at the end of
`_init_schema`, after `_migrate_feedback_routes_table`, in its own short transaction:

```sql
CREATE TABLE IF NOT EXISTS feedback_rfas (
    question_key TEXT NOT NULL,
    cause        TEXT NOT NULL,
    entity_type  TEXT NOT NULL,
    entity_slug  TEXT NOT NULL,
    rfa_id       TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    PRIMARY KEY (question_key, cause, entity_type, entity_slug)
);
```

Same statement on SQLite and Postgres. In the `resource_explorer` schema, like every other registry table.

### What it holds

One row per RFA that routing raised: the question's key, the cause (`catalog_gap` or `unclear_question`), the entity
it was raised against, and the RFA's activity id (`pending` while a Save is raising it).

The key is the gaps' `check_name` form: `question:<catalog id>`, or for an uncatalogued question the text
(`question:<text>`). **In a private context** (a private investigation, or a zone restricting READ) it is the catalog
id or `question:sha1:<12 hex>`, never the text. So this table holds question text only for questions that were not
private, which is the same text the RFA's own summary carries by the project owner's Q8 decision.

The primary key makes one RFA per question: a claim is `INSERT ... ON CONFLICT DO NOTHING` before the RFA is written,
so two Saves at once raise one RFA, and a later answer on the same question is added to that RFA's `items`.

### What it touches

Nothing existing is altered. Routing also writes to two existing tables, with no schema change:
`activity_log` (the RFA itself, through `log_rfa`, and an item appended when a later answer joins it) and
`analysis_gaps.rfa_activity_id` (a catalog gap's RFA is stamped on its gap, through `mark_gap_rfa`).

### Lock behaviour

`CREATE TABLE IF NOT EXISTS` takes no lock on any existing table. A second init is a no-op.

### Rollback

`DROP TABLE feedback_rfas;`. The RFAs it raised stay in the activity log and the drawer. Without the table, a later
answer on the same question would raise a second RFA rather than join the first.
