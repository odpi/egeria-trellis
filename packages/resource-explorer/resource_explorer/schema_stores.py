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


def _rows_for(names) -> list[dict]:
    from resource_explorer.config import get_config
    dimension = int(get_config().embeddings.dimension)
    return [{"name": n, "dimension": dimension, "metric": METRIC, "hnsw_m": HNSW_M, "hnsw_ef_construction": HNSW_EF_CONSTRUCTION}
            for n in dict.fromkeys(names)]


def declared_collection_names(projects) -> list[str]:
    """Every vector collection RE will write for these projects (dicts with slug, homepage_url, docs_url, collections), without repeats:
    * `<slug>_<type>` for each collection type (repositories);
    * `site_collection_name(<url>)` for the project's docs_url and homepage_url. Website ingestion keys its collection on the HOST of
      the one it picks (docs_url if reachable, else homepage_url), not on a slug, so both are declared: known before the first ingest;
    * the collection names the project has recorded (a site shared with a sibling repo, a host that was renamed).
    A new host, project or URL changes this list: the next schema apply declares and creates it (a new resource needs the schema Job)."""
    from resource_explorer.configdata.collection_config import COLLECTION_TYPES
    from resource_explorer.ingestion.site_discovery import site_collection_name
    names: list[str] = []
    for p in projects:
        names += [collection_name(p["slug"], ctype) for ctype in COLLECTION_TYPES]
        for field in ("docs_url", "homepage_url"):
            url = (p.get(field) or "").strip()
            if url:
                names.append(site_collection_name(url))
        names += [str(c) for c in (p.get("collections") or []) if c]
    return list(dict.fromkeys(names))


def declared_collection_rows(slugs, extra_names=()) -> list[dict]:
    """One row per collection RE will use: name, dimension, metric, HNSW settings. `slugs` give the repository collections
    (`<slug>_<type>`); `extra_names` are declared as given (site collections, recorded names)."""
    from resource_explorer.configdata.collection_config import COLLECTION_TYPES
    return _rows_for([collection_name(slug, ctype) for slug in slugs for ctype in COLLECTION_TYPES] + list(extra_names))


# ── the extension ──────────────────────────────────────────────────────────────────────────────────────────────────

def ensure_extension(conn, dbname: str = "") -> int:
    """Make sure `vector` is installed; returns the statements sent (0 or 1). If it is, send nothing. If not, try to create it; on a privilege error say what to
    ask for instead of passing the raw permission error on (a managed Postgres often will not let the Job's role do it)."""
    import psycopg2.errors
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        if cur.fetchone():
            return 0
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.commit()
        return 1
    except psycopg2.errors.InsufficientPrivilege as exc:
        note = ""
        try:
            conn.rollback()
        except psycopg2.Error as rollback_error:      # a broken connection: say so in the message instead of dropping it
            note = f" (the connection also failed to roll back: {type(rollback_error).__name__})"
        where = f" in database {dbname!r}" if dbname else ""
        raise SchemaNotReady(
            f"the pgvector extension is not installed{where} and this role may not create it: "
            "ask the database admin to run CREATE EXTENSION vector, then run `resource-explorer schema apply` again" + note) from exc


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
    """One schema step for everything: the registry (`schema_apply`), then metrics, feedback, vectorstore (extension, schema, the declared-
    collections table) and artifact_tree, and one marker row per store. Returns the statement counts: {"registry", "stores", "metrics", "feedback",
    "vectorstore", "artifact_tree"}.

    The markers say the stores' DDL is at this code's version, so a second run skips those. The collections are reconciled EVERY run: a project,
    a URL or a host added since the last run is declared and its tables are created, because in `external` mode the pods cannot (TR-129/130)."""
    reg = ProjectRegistry.open_for_schema_apply(**({"database_url": url} if url else {}))
    n_registry, _secs = reg.schema_apply()
    if not reg.database_url.startswith("postgresql"):
        # A from-scratch SQLite environment has no shared Postgres: the vector store and the artifact tree need one, so
        # there is nothing of theirs to apply and no marker rows for them (`require_ready` is a Postgres-mode check).
        return {"registry": n_registry, "stores": 0}
    fps = fingerprints()
    have = _read_markers(reg)
    with reg._conn() as conn:
        projects = [{"slug": r[0], "homepage_url": r[1] or "", "docs_url": r[2] or "", "collections": json.loads(r[3] or "[]")}
                    for r in conn.execute("SELECT slug, homepage_url, docs_url, collections FROM projects").fetchall()]
    drifted = stores_drifted(have, fps) or (have.get(COMBINED_ROW) or ("",))[0] != fps["combined"]
    from resource_explorer.config import get_config
    cfg = get_config()
    pg = cfg.pgvector
    detail: dict[str, int] = {n: (have.get(row) or ("", 0))[1] for n, row in STORE_ROWS.items()}
    if drifted:
        from resource_explorer.feedback_store import APP, EXTERNAL_SOURCES
        from resource_explorer.observability.metrics_collector import MetricsCollector, resolve_metrics_url
        from trellis_feedback import FeedbackStore as LibraryFeedbackStore
        detail["metrics"] = MetricsCollector(database_url=resolve_metrics_url(), ddl="auto").apply_schema()
        detail["feedback"] = LibraryFeedbackStore(cfg.feedback.database_url, APP, external_sources=EXTERNAL_SOURCES, ddl="auto").apply_schema()
        from resource_explorer.ingestion.artifact_tree_sink import _store as _tree_store
        detail["artifact_tree"] = _tree_store(ddl="auto").apply_schema()
    # The vector store's own schema, the declared-collections table and the collections: reconciled EVERY run, on what physically exists.
    # Tables first, then declare: a Job that dies in between leaves tables nobody declared (harmless, found again next run), never a
    # declared name without its table (the pods would pass the declaration check and then fail on insert; nothing would repair it).
    table = f'"{pg.schema_name}"."{COLLECTIONS_TABLE}"' if pg.schema_name else f'"{COLLECTIONS_TABLE}"'
    sent = 0
    conn = _pg_connect(pg)
    try:
        sent += ensure_extension(conn, pg.dbname)
        with conn.cursor() as cur:
            if pg.schema_name:
                cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{pg.schema_name}"')
                sent += 1
            cur.execute(_COLLECTIONS_DDL.format(table=table))
            sent += 1
            cur.execute(f"SELECT name FROM {table}")
            declared = {r[0] for r in cur.fetchall()}
            wanted = list(dict.fromkeys(declared_collection_names(projects) + sorted(declared)))   # declared names keep their tables too
            # missing: no table, or no vector index (a run killed between the two)
            qual = "quote_ident(%s) || chr(46) || " if pg.schema_name else ""
            params = [wanted, pg.schema_name, pg.schema_name] if pg.schema_name else [wanted]
            cur.execute(
                f"SELECT n FROM unnest(%s::text[]) AS t(n) WHERE to_regclass({qual}quote_ident(n)) IS NULL "
                f"OR to_regclass({qual}quote_ident(n || '_embedding_idx')) IS NULL",
                params)
            missing = [r[0] for r in cur.fetchall()]
        conn.commit()
    finally:
        conn.close()
    from resource_explorer.vector_store_pg import PgVectorStore
    applied = PgVectorStore(ddl="auto").apply_schema(missing)      # tables and indexes first
    sent += applied
    new_rows = [r for r in declared_collection_rows([], wanted) if r["name"] not in declared]
    if new_rows:
        conn = _pg_connect(pg)
        try:
            with conn.cursor() as cur:
                for r in new_rows:
                    cur.execute(
                        f"INSERT INTO {table} (name, dimension, metric, hnsw_m, hnsw_ef_construction) VALUES (%s, %s, %s, %s, %s) "
                        "ON CONFLICT (name) DO NOTHING",
                        (r["name"], r["dimension"], r["metric"], r["hnsw_m"], r["hnsw_ef_construction"]))
            conn.commit()                                           # declared only now that their tables exist
        finally:
            conn.close()
    if drifted:
        detail["vectorstore"] = sent
        _write_markers(reg, fps, detail)
    else:
        # what the new collections cost: the statements apply_schema sent, less the two its own connect() sends (extension, schema)
        detail["vectorstore"] += max(0, applied - (2 if pg.schema_name else 1))
    return {"registry": n_registry, "stores": len(STORE_ROWS) if drifted else 0, **detail}
