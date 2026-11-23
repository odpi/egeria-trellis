"""
Consolidated PostgreSQL database connection manager and schema initializer for Egeria Advisor.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional, Tuple
from loguru import logger
import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

from advisor.config import EA_PG_OPTIONS, settings

_CREATE_SCHEMAS_DDL = """
-- 1. Query Metrics Table with Audit logging
CREATE TABLE IF NOT EXISTS query_metrics (
    id SERIAL PRIMARY KEY,
    timestamp DOUBLE PRECISION NOT NULL,
    query_text TEXT NOT NULL,
    collection_name VARCHAR(100),
    latency_ms DOUBLE PRECISION NOT NULL,
    cache_hit BOOLEAN NOT NULL,
    success BOOLEAN NOT NULL,
    query_type VARCHAR(50),
    error_message TEXT,
    result_count INTEGER,
    embedding_time_ms DOUBLE PRECISION,
    search_time_ms DOUBLE PRECISION,
    llm_time_ms DOUBLE PRECISION,
    avg_relevance_score DOUBLE PRECISION,
    sources_json TEXT,
    -- Audit / Context columns
    active_perspective VARCHAR(100),
    resolved_intent VARCHAR(50),
    routing_agent VARCHAR(100),
    applied_policy_rule JSONB,
    perspective_history JSONB,
    session_id VARCHAR(100),
    user_id VARCHAR(100),
    rating VARCHAR(50),
    star_rating INTEGER,
    suggested_collection VARCHAR(100),
    feedback_text TEXT
);

-- 2. Code Symbols Store Table
CREATE TABLE IF NOT EXISTS code_symbols (
    id SERIAL PRIMARY KEY,
    collection VARCHAR(100) NOT NULL,
    file_path TEXT NOT NULL,
    language VARCHAR(50) NOT NULL DEFAULT 'python',
    kind VARCHAR(50) NOT NULL, -- class | function | method
    name VARCHAR(200) NOT NULL,
    qualified_name VARCHAR(500) NOT NULL,
    signature TEXT NOT NULL DEFAULT '',
    docstring TEXT NOT NULL DEFAULT '',
    parent_class VARCHAR(200) NOT NULL DEFAULT '',
    return_type VARCHAR(200) NOT NULL DEFAULT '',
    start_line INTEGER NOT NULL DEFAULT 0,
    end_line INTEGER NOT NULL DEFAULT 0,
    is_private BOOLEAN NOT NULL DEFAULT FALSE,
    is_async BOOLEAN NOT NULL DEFAULT FALSE,
    complexity INTEGER NOT NULL DEFAULT 0,
    UNIQUE(collection, file_path, qualified_name)
);
CREATE INDEX IF NOT EXISTS idx_cs_collection_kind ON code_symbols(collection, kind);
CREATE INDEX IF NOT EXISTS idx_cs_collection_name ON code_symbols(collection, name);
CREATE INDEX IF NOT EXISTS idx_cs_parent_class    ON code_symbols(collection, parent_class);

-- 3. Code Relationships Table (Inheritance & Containment)
CREATE TABLE IF NOT EXISTS code_relationships (
    id SERIAL PRIMARY KEY,
    collection VARCHAR(100) NOT NULL,
    relationship_type VARCHAR(50) NOT NULL, -- inherits_from | contains_method
    source_name VARCHAR(500) NOT NULL, -- e.g. child class qualified_name, or class qualified_name
    target_name VARCHAR(500) NOT NULL, -- e.g. parent class name, or method name
    UNIQUE(collection, relationship_type, source_name, target_name)
);
CREATE INDEX IF NOT EXISTS idx_cr_source ON code_relationships(collection, source_name);
CREATE INDEX IF NOT EXISTS idx_cr_target ON code_relationships(collection, target_name);

-- 4. Incremental Indexing Change Tracking Tables
CREATE TABLE IF NOT EXISTS indexed_files (
    file_path TEXT NOT NULL,
    collection_name VARCHAR(100) NOT NULL,
    last_modified DOUBLE PRECISION NOT NULL,
    content_hash TEXT NOT NULL,
    last_indexed DOUBLE PRECISION NOT NULL,
    chunk_count INTEGER NOT NULL,
    entity_ids TEXT NOT NULL,
    PRIMARY KEY(file_path, collection_name)
);
CREATE INDEX IF NOT EXISTS idx_if_collection ON indexed_files(collection_name);

CREATE TABLE IF NOT EXISTS index_metadata (
    collection_name VARCHAR(100) PRIMARY KEY,
    last_full_index DOUBLE PRECISION NOT NULL,
    total_files INTEGER NOT NULL,
    total_chunks INTEGER NOT NULL
);

-- 5. Remaining Monitoring & Admin Tables
CREATE TABLE IF NOT EXISTS collection_health (
    collection_name VARCHAR(100) PRIMARY KEY,
    last_check DOUBLE PRECISION NOT NULL,
    entity_count INTEGER NOT NULL,
    health_score DOUBLE PRECISION NOT NULL,
    storage_size_mb DOUBLE PRECISION NOT NULL,
    last_update DOUBLE PRECISION NOT NULL,
    status VARCHAR(50) NOT NULL
);

CREATE TABLE IF NOT EXISTS system_metrics (
    timestamp DOUBLE PRECISION PRIMARY KEY,
    cpu_percent DOUBLE PRECISION NOT NULL,
    memory_percent DOUBLE PRECISION NOT NULL,
    gpu_percent DOUBLE PRECISION,
    disk_io_read_mb DOUBLE PRECISION NOT NULL,
    disk_io_write_mb DOUBLE PRECISION NOT NULL,
    network_sent_mb DOUBLE PRECISION NOT NULL,
    network_recv_mb DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS error_log (
    id SERIAL PRIMARY KEY,
    timestamp DOUBLE PRECISION NOT NULL,
    error_type VARCHAR(100) NOT NULL,
    error_message TEXT NOT NULL,
    stack_trace TEXT,
    context TEXT
);

CREATE TABLE IF NOT EXISTS plan_events (
    id SERIAL PRIMARY KEY,
    timestamp DOUBLE PRECISION NOT NULL,
    doc_id VARCHAR(100) NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    title VARCHAR(500),
    command_families TEXT,
    outcome_status VARCHAR(50),
    perspective VARCHAR(100)
);

CREATE TABLE IF NOT EXISTS ingest_log (
    collection_name VARCHAR(100) PRIMARY KEY,
    last_ingested DOUBLE PRECISION NOT NULL,
    files_processed INTEGER,
    chunks_created INTEGER,
    source VARCHAR(200)
);

-- 6. Session store (docs/runtime-architecture-plan.md §4 / advisor/session_state.py)
-- user_id persistent, session_id ephemeral (dies with the tab) — see
-- docs/design/SESSION_AND_INTERACTION_STATE.md. Postgres-backed (not the
-- design doc's original in-memory Dict) because a request can land on any
-- web worker. expires_at tracks the owning JWT's own expiry (Egeria's 1h
-- cap, see advisor/auth.py) so a session never outlives the token that
-- authenticated it.
CREATE TABLE IF NOT EXISTS advisor_sessions (
    session_id   VARCHAR(100) PRIMARY KEY,
    user_id      VARCHAR(100),
    created_at   DOUBLE PRECISION NOT NULL,
    last_seen_at DOUBLE PRECISION NOT NULL,
    state        JSONB NOT NULL DEFAULT '{}'::jsonb,
    expires_at   DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_advisor_sessions_user_id    ON advisor_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_advisor_sessions_expires_at ON advisor_sessions(expires_at);
"""


class ConsolidatedDBManager:
    """Manages central PostgreSQL connection pool and schemas."""

    def __init__(self):
        self.host = settings.pgvector_host
        self.port = settings.pgvector_port
        self.dbname = settings.pgvector_dbname
        self.user = settings.pgvector_user
        self.password = settings.pgvector_password
        self._max_connections = settings.pgvector_max_connections

        self._pool: Optional[ThreadedConnectionPool] = None
        self._lock = threading.Lock()

    def connect(self) -> None:
        """Initialize connection pool if not already initialized."""
        if self._pool is not None:
            return
        from advisor.config import require_pgvector_password

        require_pgvector_password(self.password)      # TR-12: refused by name, not as libpq's auth error
        with self._lock:
            if self._pool is not None:
                return
            try:
                logger.info(f"Connecting consolidated DB manager to PostgreSQL at {self.host}:{self.port}/{self.dbname}")
                self._pool = ThreadedConnectionPool(
                    minconn=1,
                    maxconn=self._max_connections,
                    host=self.host,
                    port=self.port,
                    dbname=self.dbname,
                    user=self.user,
                    password=self.password,
                    options=EA_PG_OPTIONS,  # TR-60a: operational SQL is unqualified; pin it to public
                )
                self._initialize_schemas()
                logger.info("Consolidated DB Manager successfully connected and initialized schemas.")
            except Exception as e:
                logger.error(f"Failed to connect Consolidated DB Manager to PostgreSQL: {e}")
                raise

    def disconnect(self) -> None:
        """Close connection pool."""
        with self._lock:
            if self._pool is not None:
                self._pool.closeall()
                self._pool = None
                logger.info("Consolidated DB Manager disconnected")

    def _initialize_schemas(self) -> None:
        """Run DDL to ensure all tables exist."""
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(_CREATE_SCHEMAS_DDL)
                # Ensure existing table has the new columns
                cur.execute("ALTER TABLE query_metrics ADD COLUMN IF NOT EXISTS active_perspective VARCHAR(100);")
                cur.execute("ALTER TABLE query_metrics ADD COLUMN IF NOT EXISTS resolved_intent VARCHAR(50);")
                cur.execute("ALTER TABLE query_metrics ADD COLUMN IF NOT EXISTS routing_agent VARCHAR(100);")
                cur.execute("ALTER TABLE query_metrics ADD COLUMN IF NOT EXISTS applied_policy_rule JSONB;")
                cur.execute("ALTER TABLE query_metrics ADD COLUMN IF NOT EXISTS perspective_history JSONB;")
                cur.execute("ALTER TABLE query_metrics ADD COLUMN IF NOT EXISTS session_id VARCHAR(100);")
                cur.execute("ALTER TABLE query_metrics ADD COLUMN IF NOT EXISTS user_id VARCHAR(100);")
                cur.execute("ALTER TABLE query_metrics ADD COLUMN IF NOT EXISTS rating VARCHAR(50);")
                cur.execute("ALTER TABLE query_metrics ADD COLUMN IF NOT EXISTS star_rating INTEGER;")
                cur.execute("ALTER TABLE query_metrics ADD COLUMN IF NOT EXISTS suggested_collection VARCHAR(100);")
                cur.execute("ALTER TABLE query_metrics ADD COLUMN IF NOT EXISTS feedback_text TEXT;")
                
                # Now create the indices on query_metrics
                cur.execute("CREATE INDEX IF NOT EXISTS idx_qm_timestamp ON query_metrics(timestamp);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_qm_resolved_intent ON query_metrics(resolved_intent);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_qm_session_id ON query_metrics(session_id);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_qm_user_id ON query_metrics(user_id);")
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to initialize PostgreSQL schemas: {e}")
            raise
        finally:
            self._pool.putconn(conn)

    def get_connection(self) -> psycopg2.extensions.connection:
        """Borrow a connection from the pool."""
        self.connect()
        return self._pool.getconn()

    def put_connection(self, conn: psycopg2.extensions.connection) -> None:
        """Return a connection to the pool."""
        if self._pool is not None:
            self._pool.putconn(conn)

    def execute_query(self, query: str, params: Optional[Tuple] = None) -> List[Dict[str, Any]]:
        """Execute a SELECT query and return list of dictionaries."""
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, params or ())
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"Consolidated DB execute_query error: {e} | Query: {query}")
            raise
        finally:
            self.put_connection(conn)

    def execute_update(self, query: str, params: Optional[Tuple] = None) -> int:
        """Execute an INSERT, UPDATE, or DELETE query and return rows affected."""
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(query, params or ())
                affected = cur.rowcount
            conn.commit()
            return affected
        except Exception as e:
            conn.rollback()
            logger.error(f"Consolidated DB execute_update error: {e} | Query: {query}")
            raise
        finally:
            self.put_connection(conn)


# Singleton DB manager instance
_db_manager: Optional[ConsolidatedDBManager] = None


def get_db_manager() -> ConsolidatedDBManager:
    """Get the singleton database manager."""
    global _db_manager
    if _db_manager is None:
        _db_manager = ConsolidatedDBManager()
    return _db_manager
