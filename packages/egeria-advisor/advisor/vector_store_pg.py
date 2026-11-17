"""
pgvector (PostgreSQL) vector store backend for Egeria Advisor.

PgVectorStore is now a thin adapter over the shared trellis-vectorstore
package (packages/trellis-vectorstore/) — extracted alongside Resource
Explorer's independently-evolved equivalent (resource_explorer/
vector_store_pg.py). See docs/trellis-vectorstore-extraction.md for the
full design rationale. This adapter's entire job is reproducing EA's exact
pre-extraction behavior: L2 distance (not RE's cosine default), unqualified
tables in `public` (not RE's named `resource_explorer` schema), and the two
collections (`pyegeria`, `pyegeria_cli`) with extra denormalized scalar
columns for fast filtered queries.

EA_COLLECTION_SCHEMAS below used to carry the exact original CREATE TABLE
strings verbatim via CollectionSchema.raw_ddl — a transitional escape hatch
for the initial cutover (see docs/trellis-vectorstore-extraction.md).
Dropped now that the generic renderer is proven equivalent: both
pyegeria/pyegeria_cli's rendered DDL was live-verified against
information_schema.columns for an identical golden baseline captured
before the cutover (the real "byte-for-byte" guarantee — Postgres doesn't
persist DDL whitespace, so this is the check that actually matters, not
string diffing) — see trellis-vectorstore-extraction.md's Phase 5.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from trellis_vectorstore import CollectionSchema, ExtraColumn, PgVectorStoreConfig, translate_filter_expr
from trellis_vectorstore.pg import PgVectorStore as _SharedPgVectorStore

from advisor.config import settings

# ---------------------------------------------------------------------------
# Per-collection schema (was _TABLE_DDL / _EXTRA_COLUMNS / _COL_DEFAULTS /
# _TABLE_NAME_MAP module-level dicts pre-extraction — now real constructor
# config passed to the shared PgVectorStore, see module docstring)
# ---------------------------------------------------------------------------

EA_COLLECTION_SCHEMAS: Dict[str, CollectionSchema] = {
    "pyegeria": CollectionSchema(
        extra_columns=(
            ExtraColumn("element_type", "VARCHAR(50)", "", "''"),
            ExtraColumn("class_name", "VARCHAR(200)", "", "''"),
            ExtraColumn("method_name", "VARCHAR(200)", "", "''"),
            ExtraColumn("module_path", "VARCHAR(500)", "", "''"),
            ExtraColumn("is_async", "BOOLEAN", False, "FALSE"),
            ExtraColumn("is_private", "BOOLEAN", False, "FALSE"),
        ),
    ),
    "pyegeria_cli": CollectionSchema(
        extra_columns=(
            ExtraColumn("main_command", "VARCHAR(100)", "", "''"),
            ExtraColumn("subcommand", "VARCHAR(200)", "", "''"),
            ExtraColumn("full_command", "VARCHAR(500)", "", "''"),
        ),
    ),
}

# Canonical name map: collection config names → pgvector table names.
# Handles the mixed-case legacy name and the old CLI collection name.
_TABLE_NAME_MAP: Dict[str, str] = {
    "pyegeria_drE": "pyegeria_dre",
    "cli_commands": "pyegeria_cli",
}


class _EaEmbeddingProvider:
    """Satisfies trellis_vectorstore.EmbeddingProvider by wrapping EA's
    existing EmbeddingGenerator (advisor/embeddings.py)."""

    def __init__(self) -> None:
        from advisor.embeddings import get_embedding_generator
        self._generator = get_embedding_generator()

    def embed_texts(self, texts):
        return self._generator.encode_batch(list(texts), show_progress=True)

    def embed_query(self, text):
        return self._generator.encode(text)


class PgVectorStore(_SharedPgVectorStore):
    """pgvector implementation of BaseVectorStore.

    Same public constructor signature EA had before the shared-package
    extraction — host/port/dbname/user/password/max_connections/ef_search,
    each defaulting to settings.pgvector_* — so every existing call site
    (the bare `PgVectorStore()` construction in get_vector_store() and
    standalone scripts) keeps working unmodified. No `schema` parameter —
    EA has never had one; tables are always unqualified/public.
    """

    def create_collection(self, collection_name: str, drop_if_exists: bool = False,
                          description: str | None = None) -> str:
        """Create (or recreate) a collection table.

        `description` is accepted for compatibility with EA's ingestion callers
        (advisor/ingest.py, data_prep/cli_indexer.py, scripts/ingest_*.py), which
        have passed it since before the store moved to trellis-vectorstore; the
        shared store dropped the parameter and every ingest path then failed
        with TypeError (trevor, 2026-09-04). The value is not persisted — the
        collection registry in advisor/collection_config.py owns descriptions.
        """
        return super().create_collection(collection_name, drop_if_exists=drop_if_exists)

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        dbname: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        max_connections: Optional[int] = None,
        ef_search: Optional[int] = None,
    ):
        config = PgVectorStoreConfig(
            host=host or settings.pgvector_host,
            port=port or settings.pgvector_port,
            dbname=dbname or settings.pgvector_dbname,
            user=user or settings.pgvector_user,
            password=password or settings.pgvector_password,
            schema=None,  # EA's tables are always unqualified/public
            max_connections=max_connections or settings.pgvector_max_connections,
            ef_search=ef_search or settings.pgvector_ef_search,
        )
        super().__init__(
            config,
            metric="l2",  # EA's original create_index() default — do not "fix" to match RE's cosine
            embeddings=_EaEmbeddingProvider(),
            collection_schemas=EA_COLLECTION_SCHEMAS,
            table_name_map=_TABLE_NAME_MAP,
            auto_provision_on_insert=False,  # EA's original insert_data() never self-provisioned
            logger=logger,  # loguru's logger already satisfies LoggerLike structurally
        )
        logger.info(f"Initialized PgVectorStore for {config.host}:{config.port}/{config.dbname}")

    def connect(self) -> None:
        """TR-12: no password, no connection: refused by name here rather than as libpq's authentication error (the
        built-in "advisor" default is gone). Construction stays side-effect free."""
        from advisor.config import require_pgvector_password

        require_pgvector_password(self._config.password)
        super().connect()

    # ------------------------------------------------------------------
    # Schema provisioning helper
    # ------------------------------------------------------------------

    def provision_schema(self, collection_names: Optional[List[str]] = None) -> None:
        """
        Create tables and HNSW indexes for all known collections.
        Called automatically on first connect; can also be called explicitly.
        """
        known = list(EA_COLLECTION_SCHEMAS.keys()) + [
            "pyegeria_dre",
            "egeria_java",
            "egeria_concepts",
            "egeria_types",
            "egeria_general",
            "egeria_workspaces",
            "egeria_templates",
        ]
        targets = collection_names or known
        for name in targets:
            self.create_collection(name)
            self.create_index(name)
        logger.info(f"✓ Provisioned schema for {len(targets)} collections")


# Kept under its pre-extraction private name — a handful of scripts import
# it this way. The real implementation now lives in trellis_vectorstore.filters.
_translate_filter_expr = translate_filter_expr
