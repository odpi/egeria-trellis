# TR-115: the four other stores under `RE_SCHEMA_MODE=external` (plan, with failing tests)

Status: **plan and red tests only.** The tests are `tests/test_tr115_external_stores.py`: 17 fail on purpose (each names a missing piece), 3 guards pass, 1 Postgres
test skips without `RE_SCHEMA_TEST_PG_URL`. Nothing in the packages is changed yet. Phase 3 (the RE chart) needs this; Phase 3 itself waits for Dan.

## The problem
TR-112b made `ProjectRegistry` honour `RE_SCHEMA_MODE=external` (no DDL; the schema Job does it). Four more places still run DDL on first use, so a pod
in `external` mode can still issue DDL, take locks, or fail on a database role that may not create things:

| Store | Where it runs DDL today | Statements |
|---|---|---|
| metrics collector (RE) | `MetricsCollector.__init__` -> `_init_schema` (`observability/metrics_collector.py`); also `shared_collector()` on every search | `CREATE TABLE IF NOT EXISTS query_log, chunk_feedback`, `ALTER TABLE ... ADD COLUMN` (4 additive migrations) |
| `trellis_feedback` | `FeedbackStore.__init__` -> `_init_schema` (`trellis_feedback/store.py`); RE builds one per request in `web/routes/feedback.py` | `CREATE SCHEMA IF NOT EXISTS`, `create_all`, one additive `ALTER` |
| `trellis_vectorstore` | `PgVectorStore.connect()` -> `_ensure_extension` (`CREATE EXTENSION IF NOT EXISTS vector`) and `_ensure_schema` (`CREATE SCHEMA`); collections' tables on `create_collection` / `auto_provision_on_insert` | extension, schema, per-collection tables and HNSW indexes |
| `trellis_artifact_tree` | `ArtifactTreeStore.create_schema()`, called by RE on every ingestion run (`ingestion/artifact_tree_sink.py`, guarded only by a per-process flag) | 4 tables, 2 indexes |

Compose must not change: the default of every new setting is today's behaviour (`auto`).

## Design
1. **A `ddl` keyword on each store, default `"auto"`** (today). `"external"` means: construction and use never send DDL. No class state, no environment
   read inside the libraries (the TR-112b lesson: class-level mode leaked between tests and threads). The keyword is explicit per instance.
   * `trellis_feedback.FeedbackStore(..., ddl="auto"|"external")`
   * `MetricsCollector(database_url=None, *, ddl="auto"|"external")`
   * `trellis_vectorstore.PgVectorStore(..., ddl="auto"|"external")`: `connect()` skips `CREATE EXTENSION` and `CREATE SCHEMA`;
     `auto_provision_on_insert=True` together with `external` is refused (a typed error naming `external`); `create_collection` is refused in `external`
     and moves to an explicit `apply_schema()` the schema Job calls.
   * `trellis_artifact_tree.ArtifactTreeStore(config, ..., ddl=...)`: in `external`, `create_schema()` raises; the Job calls `apply_schema()`;
     `check_schema()` is the read-only check (tables exist).
2. **RE glue in one place: `resource_explorer/schema_stores.py`** with `apply_all(url)` (called by `schema apply`, after the registry setup, in the same
   advisory-locked run) and `require_ready(url)` (called by the RE facades when `RE_SCHEMA_MODE=external`). RE's facades pass `ddl="external"` when the mode is
   external, and read the mode through the one function the registry already uses (`RE_SCHEMA_MODE`, validated; `apply` is never accepted from the environment).
3. **One marker, one hash.** Extend the existing `schema_version` marker rather than adding tables: the version hash already follows `_init_schema` and what it
   reaches; it must also follow the DDL of the four stores, which live in other packages (`trellis_feedback`, `trellis_vectorstore`, `trellis_artifact_tree`), so
   `schema_fingerprint.py` learns to follow those packages' schema entry points (more than one root per store, and a plain function root for `trellis_artifact_tree.create_schema_sql`;
   today it takes one class method). `schema_stores.fingerprints()` returns the registry's, each store's own and the `combined` hash. The marker stores the combined hash and, in its detail,
   each store's own fingerprint, so `require_ready` can say **which store drifted**, not only that something did. `require_ready` is then one `SELECT` on the marker, the same check the
   registry makes. Red tests: a changed DDL statement in each of the four packages moves the combined hash and that store's fingerprint and no other; comments and docstrings move nothing
   (this is the TR-112b read-back W1 miss, not repeated).
   Existence checks alone cannot see the additive `ALTER`s (metrics, feedback), so they are not enough.
4. **`schema apply` output** counts the extra statements (`schema ready · N statements`), and its second run stays a no-op.
5. **The extension.** `CREATE EXTENSION vector` needs privileges the runtime role may not have. The schema Job uses the owner role; external pods need only `USAGE`. The
   chart (Phase 3) gives the Job and the Deployment different database Secrets; this plan documents the requirement, the chart does not exist yet.

## Finding: the vectorstore collections are dynamic
RE names a collection `<resource slug>_<type>` (`vector_store_pg.collection_name`) and runs with `auto_provision_on_insert=True`, so today the first ingest of a NEW resource creates its
tables and HNSW indexes at runtime. The set of tables is therefore open-ended, and in `external` mode `create_collection` is refused: a running pod's first new collection would break.
* **A declared table lists the collections RE uses**: `schema_stores.COLLECTIONS_TABLE`, one row per collection with its name, embedding dimension, metric (cosine, RE's decision D2),
  HNSW `m` and `ef_construction`. The schema Job creates the tables and indexes from that list (`apply_schema()`), from the registry's resources (`schema_stores.declared_collection_rows`).
* **An insert into a collection not on the list fails loudly** before any write, with a typed error naming the collection and saying to run `resource-explorer schema apply`; no DDL is sent
  (`PgVectorStore(..., ddl="external", declared_collections_table=...)`). Never a silent skip, never a half-written batch.
* **Consequence to decide (D4):** adding a resource then needs a schema apply before its first ingest. Either ingestion triggers/awaits the Job (a step in the ingestion flow), or the runtime
  role keeps a narrow right to create tables in the vector schema only (then `external` is "no DDL except declared collection tables"). Recommended: the first, because the whole point of the Job is that pods hold no DDL right.

## The extension on a managed Postgres
`CREATE EXTENSION vector` is often not allowed to the Job's role (managed Postgres). `schema_stores.ensure_extension(conn)` first looks in `pg_extension`; if `vector` is installed
it does nothing; if not, it tries to create it, and on a privilege error it fails with a message that says **"ask the database admin to run CREATE EXTENSION vector"** (with the database
name), not the raw permission error. Red tests: a missing extension with a refusing role, and an installed extension that is not created again.

## Decisions for Dan (recommendations from the Architect; not made here)
* **D1. Marker.** One combined hash over all five, AND each store's own fingerprint stored in the marker's detail so `require_ready` says which store drifted. (Versus a marker per store,
  which only helps if the stores ship separately.) Recommended: one hash plus per-store detail.
* **D2. Who checks.** RE alone checks readiness (the marker) before it builds a store. The libraries promise only "no DDL with `ddl="external"`" plus a typed refusal for the operations
  that would need DDL (`create_collection`, `create_schema`, `auto_provision_on_insert`). They never read RE's marker or the environment. Recommended: this.
* **D3. Egeria Advisor.** Later, with the EA chart. **Risk to carry:** EA and RE share `trellis_feedback` and `trellis_vectorstore`. In any cluster where RE runs `external`, EA must also run
  `external`, or its DDL must be in the same schema Job; otherwise EA's `auto` mode keeps issuing DDL on the shared tables and schemas. Compose stays all-`auto`.
* **D4. New collections** (the finding above): ingestion waits for the schema Job, or the runtime role keeps a right to create vector-schema tables. Recommended: the Job.

## Order of work (after the plan is approved)
1. `trellis_feedback` (smallest; tests 2-3) -> 2. `MetricsCollector` (test 1) -> 3. `trellis_artifact_tree` + the sink (tests 4-5) ->
4. `trellis_vectorstore`, including the declared-collections table and the typed refusal -> 5. `schema_stores.py` (apply, require_ready, `ensure_extension`, `fingerprints`, `declared_collection_rows`), the fingerprint extension and `schema apply` (the Postgres end-to-end) ->
6. rebuild the RE image and prove it on the throwaway Postgres, then on kind with the schema Job (Phase 3).
Each step is its own commit; the full RE suite must stay green after each (the TR-112b gate b479 lesson: run the whole suite, never dismiss a failure as flaky).

## Risks
* Library packages are shared with Advisor: keep `ddl="auto"` byte-identical (the guard tests plus each package's own suite).
* A new additive migration in a store now needs the schema Job to run before pods roll; the existing "rolling upgrades rely on additive migrations" rule applies unchanged.
* `shared_collector()` is hit on every request: in `external` it must not do even a `SELECT` per call beyond the first (cache the readiness result per URL per process, like the registry).
