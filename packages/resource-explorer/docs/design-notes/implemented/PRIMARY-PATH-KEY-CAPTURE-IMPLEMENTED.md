# Primary-path PK/FK capture drops keys — implemented

**Coordinator brief:** `BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md` §A (2026-09-27),
a fresh coordinator hand-off after PR #315 closed three doc gaps in the
brief itself. A before B (B reads what A's fix changes) — this is A.
**Branch:** `re/primary-path-key-capture`. **PR:** #TBD.

## What was wrong

`PostgreSQLConnection._get_tables_for_schema()` built its PK/FK lookups
from `information_schema.table_constraints` / `key_column_usage` /
`constraint_column_usage`. `constraint_column_usage` is keyed by the
**referenced** side, not the referencing column, so a heavily-referenced
table (`person.businessentity`, referenced by five different FK
constraints on AdventureWorks) multiplied or collapsed rows in the join.
Verified live against `laz_local_adventureworks`
(`/Applications/Postgres.app/Contents/Versions/16/bin/psql -p 5432 -U
dwolfson -d adventureworks`):

```
truth (pg_constraint):  91 FK columns, 181 PK columns
stored (before fix):    71 FK columns,  99 PK columns
```

The catalog-only fallback added in Slice 21b (`_catalog_keys_for_schema()`,
reading `pg_constraint`/`pg_attribute` directly, keyed by the referencING
column via `conkey`/`confkey` with `WITH ORDINALITY` for composite
matching) already computed this correctly, but only ran when a table was
invisible to `information_schema` (a partial-privilege credential).

## Fix

- `_get_tables_for_schema()` now calls `_catalog_keys_for_schema()`
  directly for PK/FK, for every table regardless of `information_schema`
  visibility. The retired `information_schema` PK/FK queries are deleted
  outright, not merely unused.
- `_catalog_keys_for_schema()`'s FK lookup changed from `{(table, column):
  dict}` (last write wins) to `{(table, column): [dict, ...]}` — a column
  carrying two distinct FK constraints (rare, legal) now keeps both. Each
  column's `foreign_key` field stays the first entry, for existing
  single-FK consumers (`result_materializer.py`, `survey_definition_
  adapter.py`, `agents/tools.py`, `web/static/index.html`); a new
  `foreign_keys` field (`None` when ≤1 entry) carries the full list for a
  future consumer.
- `_catalog_columns_for_table()` (the fallback's own column builder)
  updated to match the new list-valued `fk_lookup` shape.
- Preserved the existing "not established, not a guessed `False`"
  convention throughout: `_catalog_keys_for_schema()` returns `None` (not
  `{}`) for either half when its own query fails, and both
  `_get_tables_for_schema()` and `_catalog_columns_for_table()` propagate
  that as `is_primary_key: None` / `foreign_key: None`, never a confident
  `False`.

## The `(None, None)` failure path (brief item A.4, added by PR #315)

`_catalog_keys_for_schema()` can fail (its own `try/except` around each of
the PK and FK catalog queries) and return `None` for either half. Now that
it is the *only* PK/FK source (the `information_schema` queries are
deleted, not kept as a fallback), a naive read of a failed lookup would
collapse to "measured zero keys" — a regression from today's behavior,
and the exact confident-wrong-answer shape this codebase's absence
conventions exist to prevent.

**Decision: surface the failure, do not fall back to the deleted
`information_schema` queries.** `_get_tables_for_schema()` and
`_catalog_columns_for_table()` both check `pk_lookup is None` /
`fk_lookup is None` explicitly and write `is_primary_key: None` /
`foreign_key: None` for every column in that schema when the catalog
query itself failed — never a guessed `False`. This is not new plumbing:
`db_derived.py`'s existing `keys_were_captured` / `keys_captured_for_table`
already read `is_primary_key is not None` as the "was this table's keys
captured at all" signal (Slice 21b's mixed-access handling), so a schema
whose catalog query fails is reported the same way a schema a native
Egeria read-back never captured keys for already is: excluded from the
relationship graph and named `reason: keys_not_captured`/"not established"
rather than "has no foreign keys." No fallback to the retired queries was
added, so deleting them (brief step 2) stands as written.

Test: `test_a_failed_catalog_key_query_stays_unestablished_not_a_guessed_false`
(pre-existing, still passing) pins this for the catalog-fallback path;
the new `test_information_schema_visible_table_still_gets_catalog_keys`
pins the primary-path shape.

## Tests

`tests/test_postgres_catalog_fallback.py` —
`test_information_schema_visible_table_still_gets_catalog_keys` (new):
a fake `information_schema`-visible table with three FK-bearing columns —
two referencing the same target table on different columns, and one
column carrying two distinct FK constraints — asserts all three are
captured intact and that no `information_schema.table_constraints` query
is ever issued. All 17 tests in the file, and the pre-existing
`test_schema_enumeration_floor.py` (7 tests), pass unchanged.
Full suite: `uv run pytest tests/ -q` → 756 passed, 5 skipped (unrelated),
0 failed — no regression in any consumer of `is_primary_key`/`foreign_key`.

## Live verification

```
$ /Applications/Postgres.app/Contents/Versions/16/bin/psql -p 5432 -U dwolfson -d adventureworks -Atc \
  "select count(distinct (conrelid, k)) from pg_constraint, unnest(conkey) k where contype='f'; \
   select count(*) from pg_constraint, unnest(conkey) where contype='p';"
91
181
```

matches the numbers `_catalog_keys_for_schema()` now feeds into
`_get_tables_for_schema()` as the primary path (unit-test-verified above;
a fresh end-to-end survey re-run against `laz_local_adventureworks` and
`localhost_docker_coco_pharma` to confirm the stored `database_columns`
counts, and the Discovery-tab Relationship Graph gate — edge count ≥ 86,
components < 8, no schema reporting `keys_not_captured` — is the
coordinator's next step, tracked as a follow-up: it needs the RE web/
worker running against both live databases, which this pass did not
start.)

## Order and size

Per the brief: A before B, since B's `load_inputs()` reads what A's fix
changes to `database_columns`. B, C, D, E are unblocked and can proceed in
parallel per the brief's own ordering note.


## Live verification (design session, 2026-09-28 01:24 UTC)

Direct `DatabaseSurveyor.survey(steps=["schema"], read_egeria_catalog=False)`
from this branch's code against both registered databases, using each
database's stored credential — a write to the shared dev registry,
disclosed here per the dev-writes ruling. Counts are over the latest
`database_columns` rows before and after:

| database | PK columns before → after | FK columns before → after | truth (pg_constraint, user schemas) |
|---|---|---|---|
| laz_local_adventureworks | 99 → 99 | 71 → **91** | PK 99, FK 91 |
| localhost_docker_coco_pharma | 24 → 24 | 13 → 13 | unchanged, no regression |

The brief's PK target of 181 was the design session's own error: it
counted `pg_catalog`'s primary keys (82 columns) alongside the user
schemas. PK capture was already complete; FK capture is now complete.

Relationship Graph derived from the new adventureworks rows with
`db_derived.derive_relationship_graph(load_inputs(...))`: 91 edges,
2 components, largest component 67 of 68 tables, one isolated table
(`production.transactionhistoryarchive`, which has no foreign keys in
AdventureWorks). Before: 71 edges, 8 components, largest 24. Gate
(edges ≥ 86, components < 8) passes on the data itself.
