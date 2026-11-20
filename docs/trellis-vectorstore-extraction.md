# Shared `trellis-vectorstore` package — extraction design

> **Status (2026-11-19, 6.3 Tech Preview):** **implemented.** `packages/trellis-vectorstore` exists and both RE and EA depend on it. Read this as the design record.

**Status: designed, not built.** README.md's "Notes for contributors" already names vector store
access as the first candidate for extraction into a shared workspace member — this doc is the
scoping pass for actually doing it, deliberately stopped short of execution. Rationale for
stopping there: this is a structural change touching the core data-access layer of two
independently-deployed, currently-live services, with genuine (not just cosmetic) schema and
distance-metric differences between them — worth a design review before either app's vector store
code gets touched, not something to execute unsupervised in the same pass as smaller, lower-risk
cleanup items.

## What's duplicated, concretely

- `resource_explorer/vector_store_pg.py` (565 lines) vs. `advisor/vector_store_pg.py` (638 lines)
  — both define a `PgVectorStore(BaseVectorStore)` with near-identical `ThreadedConnectionPool`
  setup, `register_vector()` calls, and HNSW index creation/connection-pooling boilerplate. RE's
  own file docstring already states it was "structurally adapted from
  egeria-advisor/advisor/vector_store_pg.py... per decision D3" — a deliberate, disclosed fork,
  not an accidental copy.
- `resource_explorer/vector_store_base.py` (114 lines) vs. `advisor/vector_store_base.py`
  (135 lines) — confirmed via direct diff to have real, not cosmetic, divergence:
  - RE uses one uniform per-collection schema (`id, embedding, text, metadata`) for every
    collection; `create_collection()`/`insert_data()` take no `extra_fields` param. EA's version
    does take `extra_fields` — its collections carry per-collection extra scalar columns RE's
    don't.
  - RE's ABC has two methods EA's doesn't: `collection_exists()` (closes a leak where a caller
    previously reached past the public interface to call a Milvus-specific `has_collection()`
    directly) and `delete_by_metadata()` (backs RE's real row-level incremental-indexing rework).
  - RE's `SearchResult` dataclass carries a `collection: str` field (populated by multi-collection
    callers) that EA's doesn't.
  - Distance metric differs: RE uses cosine distance uniformly; EA's per-collection extra columns
    imply L2 distance is in play for at least some collections (confirmed in the earlier
    architecture-session findings, not re-verified line-by-line here).

## Why this isn't a trivial extraction

A shared package can't just hoist one file's `PgVectorStore` and delete the other — it has to
express the real differences (uniform vs. per-collection-extra-columns schema, cosine vs. L2
distance, RE's two additional ABC methods) as *configuration* the shared class takes, not paper
over them. Getting that abstraction wrong risks a regression that's hard to catch locally — the
correct fix if a real regression surfaced later would need live verification against both apps'
actual running Postgres instances (`egeria-shared-postgres`), which isn't something to attempt
without the user present.

## Recommended approach, if/when this gets built

1. New workspace member `packages/trellis-vectorstore/` (first entry under a `packages/*` glob
   that isn't `resource-explorer` or `egeria-advisor` — this repo has no prior extracted-package
   precedent, so this also sets the pattern: workspace member wiring, versioning, how the two apps
   depend on it via `[tool.uv.sources]`, per README's own stated intent).
2. A single `PgVectorStore` parameterized by:
   - `schema: dict` — column definitions beyond the uniform base four, so EA's per-collection
     `extra_fields` shape and RE's uniform shape are both just configurations of one class, not
     two branches of hand-written SQL.
   - `distance_metric: Literal["cosine", "l2"]` — per-store or per-collection, matching whichever
     granularity EA's current L2 usage actually needs (verify this at implementation time, not
     assumed here).
3. `BaseVectorStore` ABC gains RE's `collection_exists()`/`delete_by_metadata()` unconditionally
   (both are generically useful, EA just never needed them yet) and an `extra_fields`
   pass-through on `create_collection()`/`insert_data()` that RE's uniform-schema callers simply
   never populate.
4. Update both packages' import sites (`multi_collection_store.py`-style wrappers exist in both —
   grep for every `PgVectorStore`/`MultiCollectionStore`/`BaseVectorStore` reference in each
   package before starting, not just the two files named above).
5. Regression-test each app's existing vector-store test suite unchanged against the new shared
   package before touching anything else; live-verify a real query round-trip against each app's
   actual collections before calling this done — this is exactly the kind of change where "tests
   pass" and "works against real data" can diverge.

**Rough estimate:** 1–2 days, mostly in step 2 (getting the schema/distance-metric abstraction
right) and step 5 (live verification against two real, independently-deployed services).

## Not scoped by this doc

The exact shape of the `schema` config dict, whether `distance_metric` needs to be per-collection
or is uniformly per-store within each app, and the workspace-member versioning/release process for
a package two independently-deployed apps both depend on (does a `trellis-vectorstore` change
require coordinated redeploys of both RE and EA, or can it be versioned so each app pins
independently?) — these are implementation-time decisions, not settled here.
