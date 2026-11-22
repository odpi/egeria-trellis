"""TR-115: the four stores beyond the registry, brought under the schema step (`resource-explorer schema apply`).

The stores: the metrics collector (RE), `trellis_feedback`, `trellis_vectorstore` (including CREATE EXTENSION vector and
the collections' tables) and `trellis_artifact_tree`. Design: docs/tr115-external-schema-plan.md.

* `fingerprints()` hashes each store's DDL-defining code (registry included) and a `combined` hash over all five.
* `apply_all()` runs the registry's schema apply, then each store's `apply_schema`, then writes one marker row per store.
* `require_ready()` is the read-only check a process makes under RE_SCHEMA_MODE=external: it names the store that drifted.
* The libraries never read this module's marker or the environment (plan decision D2): RE alone decides.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from typing import Callable

from resource_explorer.registry import ProjectRegistry, SchemaNotReady
from resource_explorer.schema_fingerprint import FOLLOWED_PACKAGES, default_source, fingerprint

#: COLLECTIONS_TABLE: the one table that lists the vector collections RE uses, in the vector store's schema (plan, "Finding").
from resource_explorer.vector_store_pg import COLLECTIONS_TABLE
_COLLECTIONS_DDL = (
    f"CREATE TABLE IF NOT EXISTS {{table}} (name TEXT PRIMARY KEY, dimension INTEGER NOT NULL, metric TEXT NOT NULL, "
    f"hnsw_m INTEGER NOT NULL, hnsw_ef_construction INTEGER NOT NULL)"
)
HNSW_M = 16                # the vector store's create_index defaults, stated here so the declared rows say what is built
HNSW_EF_CONSTRUCTION = 64
METRIC = "cosine"           # RE's decision D2

#: store -> marker row id in `schema_version` (row 1 is the registry's, written by ProjectRegistry.schema_apply).
STORE_ROWS = {"metrics": 2, "feedback": 3, "vectorstore": 4, "artifact_tree": 5}
COMBINED_ROW = 6

# store -> (entry module, class or None, root members/functions, followed packages)
_STORE_ROOTS = {
    "metrics": ("resource_explorer.observability.metrics_collector", "MetricsCollector", ("_init_schema",), ("resource_explorer",)),
    "feedback": ("trellis_feedback.store", "FeedbackStore", ("_init_schema", "_add_missing_columns"), FOLLOWED_PACKAGES),
    "vectorstore": ("trellis_vectorstore.pg", "PgVectorStore",
                    ("_ensure_extension", "_ensure_schema", "_render_ddl", "create_collection", "create_index"), FOLLOWED_PACKAGES),
    "artifact_tree": ("trellis_artifact_tree.schema", None, ("create_schema_sql",), FOLLOWED_PACKAGES),
}

_lock = threading.Lock()
_ready: set[str] = set()


def fingerprints(source_of: Callable[[str], str | None] = default_source) -> dict[str, str]:
    """{"registry", "metrics", "feedback", "vectorstore", "artifact_tree", "combined"}. `source_of` serves module source
    (tests change one statement in one module and see which keys move)."""
    out = {"registry": fingerprint(ProjectRegistry.__module__, ProjectRegistry.__name__, source_of=source_of)}
    for name, (module, klass, roots, packages) in _STORE_ROOTS.items():
        out[name] = fingerprint(module, klass, source_of=source_of, roots=roots, packages=packages)
    # The declared-collections table is DDL this module sends for the vector store: it moves that store's fingerprint.
    out["vectorstore"] = hashlib.sha256((out["vectorstore"] + _COLLECTIONS_DDL).encode()).hexdigest()
    blob = "\n".join(f"{k}={out[k]}" for k in sorted(out))
    out["combined"] = hashlib.sha256(blob.encode()).hexdigest()
    return out


# ── the declared vector collections ────────────────────────────────────────────────────────────────────────────────

def collection_name(slug: str, collection_type: str) -> str:
    """The same name `MultiCollectionStore.collection_name` gives: `<slug with non [a-z0-9_] as _>_<type>`."""
    return f"{re.sub(r'[^a-z0-9_]', '_', slug.lower())}_{collection_type}"


def declared_collection_rows(slugs) -> list[dict]:
    """One row per collection RE will use for these resources: name, dimension, metric, HNSW settings."""
    from resource_explorer.config import get_config
    from resource_explorer.configdata.collection_config import COLLECTION_TYPES
    dimension = int(get_config().embeddings.dimension)
    return [
        {"name": collection_name(slug, ctype), "dimension": dimension, "metric": METRIC,
         "hnsw_m": HNSW_M, "hnsw_ef_construction": HNSW_EF_CONSTRUCTION}
        for slug in slugs for ctype in COLLECTION_TYPES
    ]


# ── the extension ──────────────────────────────────────────────────────────────────────────────────────────────────

def ensure_extension(conn, dbname: str = "") -> None:
    """Make sure `vector` is installed. If it is, send nothing. If not, try to create it; on a privilege error say what to
    ask for instead of passing the raw permission error on (a managed Postgres often will not let the Job's role do it)."""
    import psycopg2.errors
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        if cur.fetchone():
            return
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.commit()
    except psycopg2.errors.InsufficientPrivilege as exc:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        where = f" in database {dbname!r}" if dbname else ""
        raise SchemaNotReady(
            f"the pgvector extension is not installed{where} and this role may not create it: "
            "ask the database admin to run CREATE EXTENSION vector, then run `resource-explorer schema apply` again") from exc


# ── markers ────────────────────────────────────────────────────────────────────────────────────────────────────────

def _write_markers(reg: ProjectRegistry, fps: dict[str, str], detail: dict[str, int]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with reg._conn() as conn:
        for name, row in STORE_ROWS.items():
            conn.execute("DELETE FROM schema_version WHERE id = ?", (row,))
            conn.execute("INSERT INTO schema_version (id, schema_hash, statements, applied_at) VALUES (?, ?, ?, ?)",
                         (row, fps[name], detail.get(name, 0), now))
        conn.execute("DELETE FROM schema_version WHERE id = ?", (COMBINED_ROW,))
        conn.execute("INSERT INTO schema_version (id, schema_hash, statements, applied_at) VALUES (?, ?, ?, ?)",
                     (COMBINED_ROW, fps["combined"], sum(detail.values()), now))


def _read_markers(reg: ProjectRegistry) -> dict[int, tuple[str, int]]:
    """{marker row id: (hash, statements)}."""
    with reg._conn() as conn:
        return {int(r[0]): (r[1], int(r[2])) for r in conn.execute("SELECT id, schema_hash, statements FROM schema_version").fetchall()}


def stores_drifted(have: dict[int, tuple[str, int]], want: dict[str, str]) -> list[str]:
    """Names of the stores whose marker row is missing or for other code."""
    return [n for n, row in STORE_ROWS.items() if (have.get(row) or ("",))[0] != want[n]]


def require_ready(url: str | None = None, *, registry: ProjectRegistry | None = None) -> None:
    """The check a process makes under RE_SCHEMA_MODE=external, once per URL per process: one SELECT on the marker table.
    Raises SchemaNotReady naming the store(s) that are missing or for other code."""
    reg = registry or ProjectRegistry(database_url=url)
    key = reg.database_url
    if key in _ready:
        return
    with _lock:
        if key in _ready:
            return
        have = _read_markers(reg)
        want = fingerprints()
        bad = stores_drifted(have, want)
        if bad:
            raise SchemaNotReady(
                f"the {', '.join(bad)} schema is not at this version: run `resource-explorer schema apply` "
                "(the chart's schema Job) first. RE_SCHEMA_MODE=external never runs DDL.")
        _ready.add(key)


# ── apply ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def _pg_connect(cfg):
    import psycopg2
    return psycopg2.connect(host=cfg.host, port=cfg.port, dbname=cfg.dbname, user=cfg.db_user, password=cfg.password,
                            connect_timeout=cfg.connect_timeout_seconds)


def apply_all(url: str | None = None) -> dict[str, int]:
    """One schema step for everything: the registry (`schema_apply`), then metrics, feedback, vectorstore (extension, schema,
    the declared-collections table and every declared collection's table and index) and artifact_tree; then one marker row per
    store. Returns {"registry": n, "stores": 4, ...}. A second run finds every marker current and sends no DDL."""
    reg = ProjectRegistry.open_for_schema_apply(**({"database_url": url} if url else {}))
    n_registry, _secs = reg.schema_apply()
    if not reg.database_url.startswith("postgresql"):
        # A from-scratch SQLite environment has no shared Postgres: the vector store and the artifact tree need one, so
        # there is nothing of theirs to apply and no marker rows for them (`require_ready` is a Postgres-mode check).
        return {"registry": n_registry, "stores": 0}
    fps = fingerprints()
    have = _read_markers(reg)
    if not stores_drifted(have, fps) and (have.get(COMBINED_ROW) or ("",))[0] == fps["combined"]:
        # Nothing to do: report the statements the first run counted, so a second run prints the same line.
        return {"registry": n_registry, "stores": 0, **{n: have[row][1] for n, row in STORE_ROWS.items()}}
    with reg._conn() as conn:
        slugs = [r[0] for r in conn.execute("SELECT slug FROM projects").fetchall()]
    detail: dict[str, int] = {}

    from resource_explorer.config import get_config
    from resource_explorer.observability.metrics_collector import MetricsCollector, resolve_metrics_url
    MetricsCollector(database_url=resolve_metrics_url(), ddl="auto").apply_schema()
    detail["metrics"] = 6

    from resource_explorer.feedback_store import APP, EXTERNAL_SOURCES
    from trellis_feedback import FeedbackStore as LibraryFeedbackStore
    cfg = get_config()
    LibraryFeedbackStore(cfg.feedback.database_url, APP, external_sources=EXTERNAL_SOURCES, ddl="auto").apply_schema()
    detail["feedback"] = 4

    pg = cfg.pgvector
    conn = _pg_connect(pg)
    try:
        ensure_extension(conn, pg.dbname)
        with conn.cursor() as cur:
            if pg.schema_name:
                cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{pg.schema_name}"')
            table = f'"{pg.schema_name}"."{COLLECTIONS_TABLE}"' if pg.schema_name else f'"{COLLECTIONS_TABLE}"'
            cur.execute(_COLLECTIONS_DDL.format(table=table))
            rows = declared_collection_rows(slugs)
            for r in rows:
                cur.execute(
                    f"INSERT INTO {table} (name, dimension, metric, hnsw_m, hnsw_ef_construction) VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (name) DO NOTHING",
                    (r["name"], r["dimension"], r["metric"], r["hnsw_m"], r["hnsw_ef_construction"]))
        conn.commit()
    finally:
        conn.close()
    from resource_explorer.vector_store_pg import PgVectorStore
    PgVectorStore(ddl="auto").apply_schema([r["name"] for r in rows])
    detail["vectorstore"] = 3 + 2 * len(rows)

    from resource_explorer.ingestion.artifact_tree_sink import _store as _tree_store
    _tree_store(ddl="auto").apply_schema()
    detail["artifact_tree"] = 6

    _write_markers(reg, fps, detail)
    return {"registry": n_registry, "stores": len(STORE_ROWS), **detail}
