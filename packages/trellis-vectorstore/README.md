# trellis-vectorstore

Shared pgvector-backed vector store, used by both `resource-explorer` and `egeria-advisor`.

Extracted from two independently-evolved but structurally similar `PgVectorStore`
implementations. See `docs/trellis-vectorstore-extraction.md` (repo root) for the full
design rationale — every constructor parameter on `PgVectorStore` traces to a confirmed,
deliberate behavioral difference between the two original implementations, not a guess.

Neither app should need to change any of its own call sites: each keeps its existing
`vector_store_pg.py`/`vector_store.py` module as a thin adapter that subclasses/configures
this package's `PgVectorStore` to reproduce its own pre-extraction behavior exactly.

## Using it

Both apps subclass rather than construct this directly — see `resource_explorer/vector_store_pg.py`
and `advisor/vector_store_pg.py` for the two live adapters. Direct use looks like:

```python
import os
from trellis_vectorstore import PgVectorStore, PgVectorStoreConfig

store = PgVectorStore(
    PgVectorStoreConfig(host="localhost", port=5442, dbname="egeria_advisor",
                        user="egeria_advisor",
                        password=os.environ["PGVECTOR_PASSWORD"],   # no default password

                        schema="resource_explorer"),   # None = unqualified, public
    metric="cosine",              # REQUIRED — no default, see below
    embeddings=my_provider,       # REQUIRED — an EmbeddingProvider
)

store.search("my_collection", query_text="how do zones work?", top_k=5)
```

**`metric` has no default on purpose.** Cosine and L2 pair a distance operator with a score
formula, and the two apps calibrated their score thresholds against different ones — inheriting
the wrong metric silently changes what `min_score` means rather than failing. `schema=None`
means unqualified identifiers in `public`; a string means schema-qualified.
