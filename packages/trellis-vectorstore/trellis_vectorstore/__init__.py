"""trellis-vectorstore — shared pgvector-backed vector store.

Extracted from Resource Explorer's and Egeria Advisor's independently-
evolved PgVectorStore implementations, which were structurally similar
(psycopg2 + pgvector + ThreadedConnectionPool + HNSW) but had real,
deliberate behavioral differences: Postgres schema qualification (RE:
named schema; EA: unqualified/public), distance metric + score formula
(RE: cosine; EA: L2), per-collection extra scalar columns (EA only), and
search()/query_by_filter() filtering capability (EA only, RE never used
it). Every one of those differences is expressed as explicit, required
constructor configuration on the shared PgVectorStore rather than baked
into two copies of near-identical code — see docs/trellis-vectorstore-
extraction.md (in the repo root) for the full design rationale, and each
app's own thin adapter (resource_explorer/vector_store_pg.py,
advisor/vector_store_pg.py) for how each app's exact pre-extraction
behavior maps onto this shared class's configuration.
"""
from __future__ import annotations

from trellis_vectorstore.base import BaseVectorStore, SearchResult
from trellis_vectorstore.config import PgVectorStoreConfig
from trellis_vectorstore.embeddings import EmbeddingProvider
from trellis_vectorstore.filters import translate_filter_expr
from trellis_vectorstore.metrics import COSINE, L2, METRICS, MetricStrategy, resolve_metric
from trellis_vectorstore.pg import PgVectorStore, SchemaNotReady
from trellis_vectorstore.schemas import CollectionSchema, ExtraColumn, render_create_table_sql

__all__ = [
    "BaseVectorStore",
    "SearchResult",
    "PgVectorStore",
    "SchemaNotReady",
    "PgVectorStoreConfig",
    "EmbeddingProvider",
    "translate_filter_expr",
    "MetricStrategy",
    "COSINE",
    "L2",
    "METRICS",
    "resolve_metric",
    "CollectionSchema",
    "ExtraColumn",
    "render_create_table_sql",
]
