# TR-115: the four other stores under `RE_SCHEMA_MODE=external` (plan, with failing tests)

Status: **plan and red tests only.** The tests are `tests/test_tr115_external_stores.py`: 8 fail on purpose (each names a missing piece), 3 guards pass, 1 Postgres
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
   `schema_fingerprint.py` learns to follow those packages' schema entry points. `require_ready` is then one `SELECT` on the marker, the same check the registry makes.
   Existence checks alone cannot see the additive `ALTER`s (metrics, feedback), so they are not enough.
4. **`schema apply` output** counts the extra statements (`schema ready · N statements`), and its second run stays a no-op.
5. **The extension.** `CREATE EXTENSION vector` needs privileges the runtime role may not have. The schema Job uses the owner role; external pods need only `USAGE`. The
   chart (Phase 3) gives the Job and the Deployment different database Secrets; this plan documents the requirement, the chart does not exist yet.

## Decisions for Dan (not made here)
* **D1.** Marker design above (one hash over all five) versus a marker per store. One hash is simpler to run and to reason about; a per-store marker lets stores
  version independently, which only matters if they ship separately. Recommended: one hash.
* **D2.** Where the stores' external check lives: libraries raise their own `SchemaNotReady`-style error (subclass of RE's) versus RE alone checking the
  marker before it builds a store. Recommended: RE alone (the libraries stay free of RE's marker); the libraries only promise "no DDL".
* **D3.** Egeria Advisor uses `trellis_vectorstore` and `trellis_feedback` too (`advisor/vector_store_pg.py`, `feedback_store.py`). This plan makes the
  libraries ready for it; wiring EA's own external mode is a later step with the EA chart, not part of TR-115.

## Order of work (after the plan is approved)
1. `trellis_feedback` (smallest; tests 2-3) -> 2. `MetricsCollector` (test 1) -> 3. `trellis_artifact_tree` + the sink (tests 4-5) ->
4. `trellis_vectorstore` (tests 6-7) -> 5. `schema_stores.py`, the fingerprint extension and `schema apply` (tests 8-9, the Postgres end-to-end) ->
6. rebuild the RE image and prove it on the throwaway Postgres, then on kind with the schema Job (Phase 3).
Each step is its own commit; the full RE suite must stay green after each (the TR-112b gate b479 lesson: run the whole suite, never dismiss a failure as flaky).

## Risks
* Library packages are shared with Advisor: keep `ddl="auto"` byte-identical (the guard tests plus each package's own suite).
* A new additive migration in a store now needs the schema Job to run before pods roll; the existing "rolling upgrades rely on additive migrations" rule applies unchanged.
* `shared_collector()` is hit on every request: in `external` it must not do even a `SELECT` per call beyond the first (cache the readiness result per URL per process, like the registry).
