"""
pgvector (PostgreSQL) vector store backend for Resource Explorer.

PgVectorStore is now a thin adapter over the shared trellis-vectorstore
package (packages/trellis-vectorstore/) — extracted alongside Egeria
Advisor's independently-evolved equivalent (advisor/vector_store_pg.py).
See docs/trellis-vectorstore-extraction.md for the full design rationale.
This adapter's entire job is reproducing RE's exact pre-extraction
behavior: cosine distance (not EA's L2 default — RE's existing min_score
threshold semantics were calibrated against Milvus's COSINE/FLAT setup,
migration plan decision D2), a named Postgres schema (not EA's unqualified
`public`, decision D1), and no per-collection extra scalar columns (RE's
uniform 4-column schema, unlike EA's `pyegeria`/`pyegeria_cli`).

MultiCollectionStore below is unchanged — it stays package-local per the
extraction design (RE's and EA's MultiCollectionStore are different
abstractions wearing the same name; only BaseVectorStore/SearchResult/
PgVectorStore moved to the shared package).

Two classes:
  - PgVectorStore: RE's adapter over the shared implementation, same public
    constructor signature as before extraction — every existing call site
    (`PgVectorStore(schema=...)` in tests/conftest.py, the migration
    script) keeps working unmodified.
  - MultiCollectionStore: thin compatibility wrapper preserving the exact
    public method names/signatures the ~15 existing call sites already use
    (search/insert/drop_collection/count/list_source_files/collection_name),
    so this migration is a mechanical import swap at every call site rather
    than a rewrite of the callers themselves.
"""

import logging
import re
import threading

from trellis_vectorstore import PgVectorStoreConfig, SearchResult
from trellis_vectorstore.pg import PgVectorStore as _SharedPgVectorStore

from resource_explorer.config import get_config


def _chunk_file(metadata: dict | None) -> str:
    """The file a chunk came from, for its id: `file_path` (code, docs, API specs, notebooks, a website's page URL,
    a file system's narrative), else `source_url`, else "" (release notes, which are per GitHub release)."""
    m = metadata or {}
    return str(m.get("file_path") or m.get("source_url") or "")


def chunk_ids(collection: str, texts: list[str], metadatas: list[dict]) -> list[str]:
    """Deterministic chunk ids, minted per FILE (BRIEF-REINDEX-FROM-SCRATCH.md, R0, the project owner's Q8).

    `sha256("<collection>:<file>:<index in file>:<text>")[:32]`. Until R0 the index was the chunk's position in the
    insert batch: a first index numbered a whole collection from 0 and a refresh numbered only its changed files from
    0, so one added chunk shifted every later id, and two refreshes could give different files' chunks the same id
    (identical text at the same batch index), which `ON CONFLICT (id)` resolved by overwriting one with the other.
    Per file, an unchanged file keeps its ids through a refresh or a rebuild, and two files can never share one.

    Every caller inserts a file's chunks in one call (pipeline, website, narrative docs, add-docs), so the index in
    file is complete. Rows written before R0 keep their old ids until their file is next re-inserted; `chunk_votes`
    carries a vote across that change. Not Python's hash(): it is randomized per process.
    """
    import hashlib
    seen: dict[str, int] = {}
    ids: list[str] = []
    for text, meta in zip(texts, metadatas):
        f = _chunk_file(meta)
        n = seen.get(f, 0)
        seen[f] = n + 1
        ids.append(hashlib.sha256(f"{collection}:{f}:{n}:{text}".encode()).hexdigest()[:32])
    return ids


#: While a vote carrier is recording (`record_inserts`), each inserted chunk's (collection, id, text, file) is appended
#: to every recorder that asked for its collection, whichever MultiCollectionStore instance inserted it. Module-level,
#: not a ContextVar: a ContextVar is not inherited by pool threads, so it would record nothing if embedding ever moves
#: to workers (the architect's read-back of 7af6ea51).
_INSERT_RECORDERS: list[tuple[frozenset[str] | None, list[tuple[str, str, str, str]]]] = []
_RECORDERS_LOCK = threading.Lock()


class record_inserts:  # a context manager, named for what it does
    """Record what is inserted while open: `with record_inserts({"c1", "c2"}) as rows:` gives a list of
    (collection, id, text, file) for inserts into those collections only. Used by `chunk_votes` to find where a voted
    chunk went after its file was re-inserted, by matching in memory rather than by searching the table.

    Name the collections being carried: with none named (None), every concurrent insert from any repository would be
    buffered, full text and all, for as long as the block runs (the architect's read-back of 7af6ea51). An empty set
    records nothing."""

    def __init__(self, collections=None) -> None:
        self._only = None if collections is None else frozenset(collections)

    def __enter__(self) -> list[tuple[str, str, str, str]]:
        self._rows: list[tuple[str, str, str, str]] = []
        self._entry = (self._only, self._rows)
        with _RECORDERS_LOCK:
            _INSERT_RECORDERS.append(self._entry)
        return self._rows

    def __exit__(self, *exc) -> None:
        with _RECORDERS_LOCK:
            _INSERT_RECORDERS.remove(self._entry)


class _ReEmbeddingProvider:
    """Satisfies trellis_vectorstore.EmbeddingProvider by wrapping RE's
    existing module-level embed_texts/embed_one functions. The import stays
    inside these methods (not at module top) deliberately — same reasoning
    as the pre-extraction code: keeps heavy ML imports (sentence-
    transformers) off this module's import path until an embedding is
    actually needed."""

    def embed_texts(self, texts):
        from resource_explorer.embeddings import embed_texts
        return embed_texts(list(texts))

    def embed_query(self, text):
        from resource_explorer.embeddings import embed_one
        return embed_one(text)


COLLECTIONS_TABLE = "vector_collections"     # schema_stores.COLLECTIONS_TABLE (kept here: schema_stores imports this module)


class PgVectorStore(_SharedPgVectorStore):
    """pgvector implementation of BaseVectorStore. One table per collection,
    all in the `resource_explorer` schema (migration plan decision D1).

    Same public constructor signature RE had before the shared-package
    extraction — host/port/dbname/user/password/schema/max_connections/
    ef_search, each defaulting to get_config().pgvector's value — so every
    existing call site (tests/conftest.py's `PgVectorStore(schema=...)`,
    scripts/migrate_vectors_milvus_to_pg.py) keeps working unmodified.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        dbname: str | None = None,
        user: str | None = None,
        password: str | None = None,
        schema: str | None = None,
        max_connections: int | None = None,
        ef_search: int | None = None,
        connect_timeout: int | None = None,
        ddl: str | None = None,
    ):
        if ddl is None:     # follow RE_SCHEMA_MODE; unset means auto, today's behaviour (TR-115)
            from resource_explorer.registry import schema_mode_from_env
            ddl = schema_mode_from_env()
        cfg = get_config().pgvector
        config = PgVectorStoreConfig(
            host=host or cfg.host,
            port=port or cfg.port,
            dbname=dbname or cfg.dbname,
            user=user or cfg.db_user,
            password=password or cfg.password,
            schema=schema or cfg.schema_name,
            max_connections=max_connections or cfg.max_connections,
            ef_search=ef_search or cfg.ef_search,
            connect_timeout=connect_timeout or cfg.connect_timeout_seconds,
        )
        super().__init__(
            config,
            metric="cosine",  # decision D2 — do not "fix" to match EA's L2 default
            embeddings=_ReEmbeddingProvider(),
            # RE's insert_with_embeddings self-provisions in auto mode; in external mode the schema Job created every
            # declared collection and an insert into an undeclared one fails loudly (TR-115, plan "Finding").
            auto_provision_on_insert=(ddl == "auto"),
            ddl=ddl,
            declared_collections_table=COLLECTIONS_TABLE if ddl == "external" else None,
        )

    def connect(self) -> None:
        """TR-12: no password, no connection: refused by name here, where it is used, rather than as libpq's
        authentication error (the built-in "advisor" default is gone). Construction stays side-effect free."""
        if not self._config.password:
            from resource_explorer.config import MissingDatabaseSetting

            raise MissingDatabaseSetting(
                "RE cannot connect to the shared Postgres (pgvector): PGVECTOR_PASSWORD is not set. Set it in the "
                ".env file. There is no built-in password any more (TR-12).")
        super().connect()


# ---------------------------------------------------------------------------
# Compatibility wrapper — preserves MultiCollectionStore's exact public
# interface so existing call sites (rag_system.py, ingestion/pipeline.py,
# agents/*.py, cli/*.py, tui/app.py, dashboard/terminal_dashboard.py,
# surveyors/file_classifier/file_classifier_surveyor.py, web/routes/projects.py)
# only need their import path changed, per migration plan Phase 1.
# ---------------------------------------------------------------------------

_shared_store: PgVectorStore | None = None
_shared_store_lock = threading.Lock()

log = logging.getLogger(__name__)

_boosts_warned = {"done": False}


def _metrics_collector():
    from resource_explorer.observability.metrics_collector import shared_collector

    return shared_collector()


def _warn_boosts_unavailable(exc: BaseException) -> None:
    """Once per process at WARNING, then at DEBUG: a metrics outage is worth one line, not one per search."""
    level = logging.DEBUG if _boosts_warned["done"] else logging.WARNING
    _boosts_warned["done"] = True
    log.log(level, "chunk feedback boosts unavailable, retrieving without them: %s: %s",
            type(exc).__name__, str(exc).splitlines()[0] if str(exc) else "")


def _get_shared_store() -> PgVectorStore:
    """A process-wide PgVectorStore singleton — connection pooling is meant
    to be shared, not re-established per MultiCollectionStore() instantiation
    (callers create one of these per call in several places)."""
    global _shared_store
    if _shared_store is None:
        with _shared_store_lock:
            if _shared_store is None:
                _shared_store = PgVectorStore()
    return _shared_store


class MultiCollectionStore:
    """
    Manages pgvector-backed collections namespaced as {project_slug}_{collection_type},
    one Postgres table per collection in the `resource_explorer` schema.

    Same public interface as the Milvus-backed version it replaces
    (multi_collection_store.py, now removed) — search/insert/drop_collection/
    count/list_source_files/collection_name — plus collection_exists(), which
    closes a leak where agents/tools.py previously reached past this
    interface to call Milvus-specific has_collection() directly, and
    delete_by_metadata(), which backs real row-level incremental re-indexing
    (migration plan decision D4 — ingestion/incremental.py) instead of the
    old collection-level drop+full-rebuild that existed specifically because
    Milvus made delete-by-filter awkward.
    """

    def __init__(self) -> None:
        self._cfg = get_config()
        self._store = _get_shared_store()

    def collection_name(self, resource_slug: str, collection_type: str) -> str:
        safe_slug = re.sub(r"[^a-z0-9_]", "_", resource_slug.lower())
        return f"{safe_slug}_{collection_type}"

    def collection_exists(self, collection: str) -> bool:
        return self._store.collection_exists(collection)

    def delete_by_metadata(self, collection: str, field: str, values: list[str]) -> int:
        return self._store.delete_by_metadata(collection, field, values)

    def count_by_metadata(self, collection: str, field: str) -> dict[str, int]:
        return self._store.count_by_metadata(collection, field)

    def search(
        self,
        query: str,
        collections: list[str],
        top_k: int | None = None,
        min_score: float | None = None,
    ) -> list[SearchResult]:
        k = top_k or self._cfg.rag.top_k
        threshold = min_score if min_score is not None else self._cfg.rag.min_score
        boosts = self._load_boosts()

        results: list[SearchResult] = []
        for collection in collections:
            for r in self._store.search(collection, query_text=query, top_k=k):
                if r.score < threshold:
                    continue
                ref = f"{collection}:{r.id}"
                boost = boosts.get(ref, 0.0)
                r.score = min(1.0, r.score + boost)
                r.collection = collection
                results.append(r)

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:k]

    def _load_boosts(self) -> dict[str, float]:
        """Per-chunk score adjustments from chat votes, read from the store MetricsCollector writes them to.

        Until 2026-11-09 this opened `observability.metrics_db` with sqlite3 while the votes went to
        METRICS_DATABASE_URL (Postgres since 2026-08-23): the connect created an empty `data/metrics.db`, the
        query failed, and a bare except returned {}, so no vote adjusted any retrieval and nothing said so
        (docs/feedback-closed-loop-design.md §1, step 0). It still fails soft, since a metrics outage must not
        stop retrieval, but it says so in the log.
        """
        try:
            return _metrics_collector().chunk_boosts()
        except Exception as exc:  # noqa: BLE001 -- retrieval must not fail because metrics are unreachable
            _warn_boosts_unavailable(exc)
            return {}

    def insert(self, collection: str, texts: list[str], metadatas: list[dict], *,
               id_collection: str | None = None) -> int:
        # Deterministic ids, minted per file (`chunk_ids`). `id_collection`: hash the ids under this name instead of
        # the table written to. A rebuild writes into `{name}__rebuild` but mints the ids the live `{name}` will hold
        # after the swap, so an unchanged chunk keeps its id and its votes (BRIEF-REINDEX-FROM-SCRATCH.md §3 step 2).
        ids = chunk_ids(id_collection or collection, texts, metadatas)
        count = self._store.insert_data(collection, texts, ids=ids, metadata=metadatas)
        if _INSERT_RECORDERS:
            with _RECORDERS_LOCK:
                wanting = [rows for only, rows in _INSERT_RECORDERS if only is None or collection in only]
                if wanting:
                    inserted = [(collection, i, t, _chunk_file(m)) for i, t, m in zip(ids, texts, metadatas)]
                    for rows in wanting:
                        rows.extend(inserted)
        return count

    def rows_by_ids(self, collection: str, ids: list[str]) -> list[dict]:
        """The stored rows (id, text, metadata) for these ids; ids not in the collection are absent. One query per id:
        it is used for the handful of voted chunks only."""
        out: list[dict] = []
        for chunk_id in ids:
            if not re.fullmatch(r"[0-9a-f]{1,64}", chunk_id or ""):
                continue                         # ids are hex; anything else cannot be one, and is not queried
            out.extend(self._store.query_by_filter(collection, f'id == "{chunk_id}"', limit=1))
        return out

    def drop_collection(self, collection: str) -> None:
        self._store.delete_collection(collection)

    def rename_collection(self, old_collection: str, new_collection: str) -> None:
        """Rename a collection's pgvector table in place, preserving its rows
        — used by the repo-rename repair operation. See
        PgVectorStore.rename_collection's docstring (trellis-vectorstore) for
        why this is a rename rather than a drop+reingest."""
        self._store.rename_collection(old_collection, new_collection)

    # The rebuild's swap (BRIEF-REINDEX-FROM-SCRATCH.md §3, R1a): one transaction for every table and index rename.
    def swap_collections(self, renames: list[tuple[str, str]], **kw) -> None:
        self._store.swap_collections(renames, **kw)

    def all_rows(self, collection: str) -> list[dict]:
        """Every row (id, text, metadata) of a collection. Used by a rebuild to carry votes, for the collections that
        have votes only."""
        return self._store.query_by_filter(collection, limit=max(1, self.count(collection)) + 1)

    def has_vector_index(self, collection: str) -> bool:
        return self._store.has_vector_index(collection)

    def table_bytes(self, collection: str) -> int:
        return self._store.table_bytes(collection)

    def check_names(self, *collections: str) -> list[str]:
        return self._store.check_names(*collections)

    def count(self, collection: str) -> int:
        return int(self._store.get_collection_stats(collection).get("num_entities", 0))

    def list_source_files(self, collections: list[str], batch_size: int = 16384) -> list[str]:
        """Return unique file_path values from metadata across the given collections."""
        import logging
        _log = logging.getLogger(__name__)
        paths: set[str] = set()
        for collection in collections:
            try:
                rows = self._store.query_by_filter(collection, limit=batch_size)
                _log.debug("list_source_files: %s → %d rows", collection, len(rows))
                for row in rows:
                    meta = row.get("metadata") or {}
                    fp = meta.get("file_path") or meta.get("source_url") or ""
                    if fp:
                        paths.add(fp)
            except Exception as exc:
                _log.warning("list_source_files: query failed for %s: %s", collection, exc)
        return list(paths)
