"""Query metrics — Postgres by default, SQLite when pointed at a file.

Was raw `sqlite3` until 2026-08-23: the last store in this package with no
portability layer, and the reason a `data/metrics.db` file kept being written
long after the registry moved to Postgres. It now uses the same SQLAlchemy
engine + ProjectRegistry.ConnectionWrapper pair the registry and FeedbackStore
use, which is what makes the SQL below backend-agnostic — the wrapper
translates `?` placeholders to `%s` and rewrites
`INTEGER PRIMARY KEY AUTOINCREMENT` to `SERIAL PRIMARY KEY` on Postgres, so
none of the statements here needed changing.

"zero external dependencies" was the original justification for SQLite. That
stopped being true of the product as a whole once the registry required
Postgres; keeping metrics on a file only meant one more store nobody was
looking at.
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine

from resource_explorer.config import get_config
from resource_explorer.registry import ConnectionWrapper


def resolve_metrics_url(cfg=None) -> str:
    """Where query_log and chunk_feedback live: an explicit sqlite:/// `metrics_db` wins, so a from-scratch or
    offline environment can keep a file; otherwise METRICS_DATABASE_URL (the shared Postgres by default).

    One resolver for the writer and every reader. Until 2026-11-09 retrieval read chunk_feedback from the old
    SQLite path while this class wrote to Postgres, so no vote adjusted any retrieval
    (docs/feedback-closed-loop-design.md §1)."""
    cfg = cfg or get_config().observability
    if cfg.metrics_db and cfg.metrics_db != "data/metrics.db":
        return f"sqlite:///{cfg.metrics_db}"
    from resource_explorer.config import require_database_url

    return require_database_url(cfg.metrics_database_url, "METRICS_DATABASE_URL", "metrics store")   # TR-12


def chunk_boost(positive: int, neutral: int, total: int) -> float:
    """A chunk's retrieval score adjustment from its votes: precision around 0.5, scaled by confidence (full at
    5+ votes), capped at ±0.15. Neutral ("partially correct") counts as half, the weighting EA's satisfaction
    formula uses (advisor/web/admin.html:570), not zero, which would score it like a negative."""
    if total <= 0:
        return 0.0
    precision = (positive + 0.5 * neutral) / total
    confidence = min(total / 5.0, 1.0)
    return (precision - 0.5) * confidence * 0.3


_shared: dict[str, MetricsCollector] = {}
#: Bumped by every vote recorded in this process, through any collector instance: a cached chunk_boosts read
#: older than the latest vote is not reused (the vote route builds its own collector).
_vote_generation = {"n": 0}
_shared_lock = threading.Lock()


def shared_collector() -> MetricsCollector:
    """One collector per metrics URL per process, for hot paths (every search, every chat turn): building one
    creates an engine and checks the schema. Keyed by URL, so a changed config (tests reset it) gets its own."""
    url = resolve_metrics_url()
    with _shared_lock:
        collector = _shared.get(url)
        if collector is None:
            collector = MetricsCollector(database_url=url)
            _shared[url] = collector
        return collector


class MetricsCollector:
    def __init__(self, database_url: str | None = None) -> None:
        cfg = get_config().observability
        self.database_url = database_url if database_url is not None else resolve_metrics_url(cfg)

        self.db_path = cfg.metrics_db
        if self.database_url.startswith("sqlite:///"):
            path_str = self.database_url[len("sqlite:///"):]
            if path_str and path_str != ":memory:":
                Path(path_str).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(self.database_url, pool_pre_ping=True)
        self._init_schema()

    @contextmanager
    def _conn(self):
        import sqlite3

        is_postgres = self.database_url.startswith("postgresql")
        raw_conn = self.engine.raw_connection()
        if not is_postgres:
            raw_conn.row_factory = sqlite3.Row
            raw_conn.execute("PRAGMA foreign_keys=ON")
        conn = ConnectionWrapper(raw_conn, is_postgres)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS query_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    query_hash TEXT NOT NULL,
                    intent TEXT,
                    project_slug TEXT,
                    latency_ms INTEGER,
                    cache_hit INTEGER DEFAULT 0,
                    response_length INTEGER,
                    chunk_refs TEXT DEFAULT '[]',
                    derivation TEXT DEFAULT '{}',
                    feedback INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chunk_feedback (
                    chunk_ref TEXT PRIMARY KEY,
                    positive_count INTEGER DEFAULT 0,
                    neutral_count INTEGER DEFAULT 0,
                    total_count INTEGER DEFAULT 0,
                    last_updated TEXT
                )
            """)
            # Migration: add chunk_refs to existing query_log tables. PRAGMA is
            # SQLite-only, so the column list comes from information_schema on
            # Postgres — same two-backend split registry._get_table_columns
            # makes, and read through this same open transaction so a table
            # created moments ago is visible.
            if conn.is_postgres:
                # current_schema() matters: without it the list is the UNION
                # of every schema's query_log, so a column added in one schema
                # (production) hides its absence in another (a test schema),
                # the ALTER is skipped, and the first UPDATE naming it fails.
                # Seen 2026-09-08 the moment a fourth migration was added.
                rows = conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'query_log' AND table_schema = current_schema()"
                ).fetchall()
                existing = {r["column_name"] for r in rows}
            else:
                # .fetchall(), not iteration: ConnectionWrapper returns a
                # cursor wrapper, and unlike the raw sqlite3 cursor this used
                # to get, it is not iterable.
                existing = {r[1] for r in conn.execute("PRAGMA table_info(query_log)").fetchall()}
            if "chunk_refs" not in existing:
                conn.execute("ALTER TABLE query_log ADD COLUMN chunk_refs TEXT DEFAULT '[]'")
            # Why these sources were selected, as distinct from chunk_refs'
            # "which chunks came back". A thumbs-down is otherwise ambiguous
            # between "the wrong questions/analyses were chosen" and "the right
            # ones were chosen and the answer was still bad" -- different fixes.
            # See docs/context-compilation-design.md §13.
            if "derivation" not in existing:
                conn.execute("ALTER TABLE query_log ADD COLUMN derivation TEXT DEFAULT '{}'")
            # The registry's context_compiles row this query's answer came
            # from; NULL when nothing was compiled or the rater did not say.
            if "compile_id" not in existing:
                conn.execute("ALTER TABLE query_log ADD COLUMN compile_id TEXT DEFAULT NULL")

            # Migration: add neutral_count to existing chunk_feedback tables
            # (2026-09 — the third, "partially correct" vote, matching EA's
            # convention of encoding neutral as feedback=0). Same
            # information_schema/PRAGMA split as above, against
            # chunk_feedback this time.
            if conn.is_postgres:
                rows = conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'chunk_feedback'"
                ).fetchall()
                cf_existing = {r["column_name"] for r in rows}
            else:
                cf_existing = {
                    r[1] for r in conn.execute("PRAGMA table_info(chunk_feedback)").fetchall()
                }
            if "neutral_count" not in cf_existing:
                conn.execute(
                    "ALTER TABLE chunk_feedback ADD COLUMN neutral_count INTEGER DEFAULT 0"
                )

    def record_query(
        self,
        query: str,
        intent: str,
        resource_slug: str | None,
        response: str,
        latency_ms: int = 0,
        cache_hit: bool = False,
        chunk_refs: list[str] | None = None,
        derivation: dict | None = None,
        usage: dict | None = None,
    ) -> None:
        """Record one query.

        derivation: the selection chain behind this answer -- Purpose and
        Perspective matched, questions raised, analysis ids dispatched to (the
        shape question_catalog_reader.get_questions() now returns per entry).
        Distinct from chunk_refs, which says what retrieval returned rather
        than why it was asked for. Optional: a query with no catalog behind it
        (free-text RAG) simply has none.
        """
        import hashlib
        query_hash = hashlib.sha256(query.encode()).hexdigest()[:16]
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO query_log
                   (timestamp, query_hash, intent, project_slug, latency_ms,
                    cache_hit, response_length, chunk_refs, derivation)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (datetime.utcnow().isoformat(), query_hash, intent, resource_slug,
                 latency_ms, int(cache_hit), len(response),
                 json.dumps(chunk_refs or []), json.dumps(derivation or {})),
            )
        # Non-blocking MLflow logging (already called from a daemon thread)
        try:
            from resource_explorer.observability.mlflow_tracking import log_query
            log_query(
                query=query,
                intent=intent,
                resource_slug=resource_slug,
                response=response,
                latency_ms=latency_ms,
                collections_used=chunk_refs or [],
                usage=usage,
            )
        except Exception:
            pass

    def record_feedback(self, query_hash: str, feedback: int, *, compile_id: str | None = None) -> None:
        """Record thumbs-up (+1), neutral/"partially correct" (0), or
        thumbs-down (-1) for a query, and update per-chunk scores.

        `feedback` follows EA's convention (`advisor/web/app.py:1815-1827`):
        > 0 positive, == 0 neutral, < 0 negative. That convention makes a
        naive `1 if feedback > 0 else 0` sign test collapse neutral into
        negative for chunk scoring — `0 > 0` is False, same branch as
        `-1 > 0` — so the three states are scored explicitly below rather
        than inferred from a sign check. See test_metrics_collector_neutral_feedback.py
        for the regression this guards.
        """
        try:
            self._record_feedback(query_hash, feedback, compile_id)
        finally:
            # After the write commits, so no search in between caches a read without this vote.
            _vote_generation["n"] += 1

    def _record_feedback(self, query_hash: str, feedback: int, compile_id: str | None) -> None:
        with self._conn() as conn:
            # Update query_log feedback column
            conn.execute(
                """UPDATE query_log SET feedback = ?,
                       compile_id = COALESCE(?, compile_id)
                   WHERE id = (SELECT id FROM query_log WHERE query_hash = ?
                               ORDER BY id DESC LIMIT 1)""",
                (feedback, compile_id, query_hash),
            )
            # Look up which chunks were retrieved for this query
            row = conn.execute(
                """SELECT chunk_refs FROM query_log WHERE query_hash = ?
                   ORDER BY id DESC LIMIT 1""",
                (query_hash,),
            ).fetchone()
            if not row:
                return
            try:
                refs = json.loads(row["chunk_refs"] or "[]")
            except Exception:
                refs = []

            # Update per-chunk feedback counts. Three explicit, mutually
            # exclusive states — never a sign test on an integer — so a
            # neutral vote can never land in the same bucket as a negative
            # one. total_count includes all three states (it's "how many
            # votes has this chunk seen"); positive_count and neutral_count
            # are separate counters so each state is independently visible
            # in the stored data, rather than folding neutral's "partial
            # credit" into positive_count at write time. EA's own 0.5
            # weighting (admin.html:570) is applied at read time instead
            # (vector_store_pg.py's boost calculation), which is where the
            # weighting is actually consumed.
            if feedback > 0:
                is_positive, is_neutral = 1, 0
            elif feedback == 0:
                is_positive, is_neutral = 0, 1
            else:
                is_positive, is_neutral = 0, 0
            now = datetime.utcnow().isoformat()
            for ref in refs:
                conn.execute(
                    """INSERT INTO chunk_feedback
                           (chunk_ref, positive_count, neutral_count, total_count, last_updated)
                       VALUES (?, ?, ?, 1, ?)
                       ON CONFLICT(chunk_ref) DO UPDATE SET
                           positive_count = chunk_feedback.positive_count + ?,
                           neutral_count = chunk_feedback.neutral_count + ?,
                           total_count = chunk_feedback.total_count + 1,
                           last_updated = ?""",
                    (ref, is_positive, is_neutral, now, is_positive, is_neutral, now),
                )

    def summary(self) -> dict:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT COUNT(*) as total,
                       AVG(latency_ms) as avg_latency,
                       SUM(cache_hit) as cache_hits,
                       AVG(CASE WHEN feedback IS NOT NULL THEN feedback END) as avg_feedback
                FROM query_log
            """).fetchone()
        return _numeric(dict(row)) if row else {}

    #: Seconds a read of chunk_feedback is reused (read-back P3: every search read the whole table). A vote
    #: recorded through this collector clears it at once, so this process never serves a score older than its
    #: own last vote; another process's vote shows within this long.
    BOOST_TTL_SECONDS = 30.0

    def chunk_boosts(self) -> dict[str, float]:
        """`chunk_ref` -> retrieval score adjustment, for every chunk with a vote (chunk_boost)."""
        cached = getattr(self, "_boost_cache", None)
        generation = _vote_generation["n"]
        if (cached is not None and cached[1] == generation
                and time.monotonic() - cached[0] < self.BOOST_TTL_SECONDS):
            return cached[2]
        boosts = self._read_chunk_boosts()
        self._boost_cache = (time.monotonic(), generation, boosts)
        return boosts

    def chunk_vote_refs(self, collection: str) -> list[str]:
        """Every `chunk_ref` with a vote in one collection (`<collection>:<id>`)."""
        prefix = collection + ":"
        with self._conn() as conn:
            # A prefix compare, not LIKE: collection names hold "_", a LIKE wildcard, and the two dialects escape it
            # differently.
            rows = conn.execute("SELECT chunk_ref FROM chunk_feedback WHERE substr(chunk_ref, 1, ?) = ? "
                                "AND total_count > 0", (len(prefix), prefix)).fetchall()
        return [r["chunk_ref"] for r in rows]

    def move_chunk_votes(self, old_ref: str, new_ref: str) -> bool:
        """Move one chunk's votes to the ref it now has (`chunk_votes`, R0): added to any the new ref already holds,
        then the old row removed, in one transaction. False when the old ref has no row."""
        if old_ref == new_ref:
            return False
        # The same naive-UTC form record_feedback writes, without utcnow()'s deprecation.
        now = datetime.now(UTC).replace(tzinfo=None).isoformat()
        with self._conn() as conn:
            row = conn.execute("SELECT positive_count, neutral_count, total_count FROM chunk_feedback "
                               "WHERE chunk_ref = ?", (old_ref,)).fetchone()
            if row is None:
                return False
            p, n, t = int(row["positive_count"] or 0), int(row["neutral_count"] or 0), int(row["total_count"] or 0)
            conn.execute(
                """INSERT INTO chunk_feedback (chunk_ref, positive_count, neutral_count, total_count, last_updated)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(chunk_ref) DO UPDATE SET
                       positive_count = chunk_feedback.positive_count + ?,
                       neutral_count = chunk_feedback.neutral_count + ?,
                       total_count = chunk_feedback.total_count + ?,
                       last_updated = ?""",
                (new_ref, p, n, t, now, p, n, t, now))
            conn.execute("DELETE FROM chunk_feedback WHERE chunk_ref = ?", (old_ref,))
        _vote_generation["n"] += 1
        return True

    def _read_chunk_boosts(self) -> dict[str, float]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT chunk_ref, positive_count, neutral_count, total_count "
                "FROM chunk_feedback WHERE total_count > 0"
            ).fetchall()
        return {r["chunk_ref"]: chunk_boost(int(r["positive_count"] or 0), int(r["neutral_count"] or 0),
                                            int(r["total_count"] or 0)) for r in rows}

    def feedback_stats(self) -> dict:
        """Summary of feedback quality across all chunks."""
        with self._conn() as conn:
            row = conn.execute("""
                SELECT COUNT(*) as chunks_with_feedback,
                       SUM(positive_count) as total_positive,
                       SUM(neutral_count) as total_neutral,
                       SUM(total_count) as total_votes
                FROM chunk_feedback WHERE total_count > 0
            """).fetchone()
        return _numeric(dict(row)) if row else {}


def _numeric(d: dict) -> dict:
    """Postgres AVG()/SUM() return Decimal where SQLite returns float/int, so
    without this the *types* in these summaries depend on the backend and
    json.dumps() raises "Object of type Decimal is not JSON serializable".
    Found on the 2026-08-23 move (the feedback store's stats() hit the same
    thing). These are numbers by contract; they come back as numbers."""
    from decimal import Decimal

    return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in d.items()}
