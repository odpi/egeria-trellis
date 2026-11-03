"""Postgres advisory-lock leader election for the worker role's loops.

The problem this closes (docs/process-model.md, Finding F2): none of the
three always-on loops — the scheduler (which per F1 also drains the
Egeria outbox and reconciles RFA actions inside its own tick), the
bootstrap monitor, and the Egeria resync scanner — took any lock. Their
``start_*`` functions are idempotent *within one process*, and nothing
at all stopped two RE processes sharing one Postgres registry from
running all three concurrently. ``run_reconciler.py``'s own docstring
calls a second process on another port "routine during development", so
the exposure was real rather than hypothetical: double-fired schedules,
duplicated RFA reconciliation, and two drains racing on outbox claims.

Mechanism
---------
``pg_try_advisory_lock(bigint)`` — **session**-scoped, not
transaction-scoped, so the lock is held for as long as the connection
that took it stays open, and Postgres releases it automatically if that
connection drops (a killed worker frees its locks without a janitor).
It never blocks: it returns false immediately if someone else holds the
key, which is exactly the "am I the leader, yes or no" question, and is
why the ``_try_`` variant is used rather than ``pg_advisory_lock``.

Each lock holds its **own dedicated connection** (``NullPool``, so
SQLAlchemy hands back a real connection rather than a pooled one that
could be recycled out from under the session). Three loops means three
connections held for the life of the process; that is the cost of
session-scoped locks and it is small against the registry's pool.

Keys
----
Derived, not hand-picked, so adding a loop cannot silently collide with
an existing key: ``blake2b(f"{KEY_NAMESPACE}:{name}", digest_size=8)``
read as a signed big-endian int64 (Postgres advisory keys are
``bigint``). The three current values are pinned by
``tests/test_leader_election.py`` so a namespace or name change is
visible as a test failure rather than as two processes quietly both
becoming leader across a rolling restart. Run
``python -m resource_explorer.leader_election`` to print them.

Not Postgres
------------
The registry is Postgres-only now (runtime-architecture-plan.md §3: the
SQLite fallback in ``registry.py`` is retired). If a code path still
constructs a SQLite registry, election degrades to a **no-op that grants
leadership**, with a warning — a single-process SQLite setup has no peer
to lose an election to, and crashing there would be a worse answer than
running the loops. No SQLite locking is implemented or intended.
"""
from __future__ import annotations

import hashlib
import logging
import threading

log = logging.getLogger(__name__)

#: Namespace for every RE advisory key. Changing this changes every key —
#: which across a rolling restart means old and new processes elect
#: separate leaders and both run. Treat it as frozen.
KEY_NAMESPACE = "resource-explorer/worker"

#: Loop names, as used for both the advisory key and the startup log line.
LOCK_SCHEDULER = "scheduler"
LOCK_BOOTSTRAP = "bootstrap-monitor"
LOCK_EGERIA_RESYNC = "egeria-resync"
#: Not a loop — a one-shot at worker startup (RE's draft GovernanceZone,
#: plan §4). It takes a lock for the same reason the loops do: N worker
#: processes starting together would otherwise each race to create the same
#: zone, and pyegeria's generic element create has no upsert.
LOCK_DRAFT_ZONE = "draft-zone-bootstrap"
#: The engine-host claim sweep (engine_host.py). A loop like the three above, but started only when
#: RE_ENGINE_HOST_ENABLED is on, so a default worker still runs exactly three loops.
LOCK_ENGINE_HOST = "engine-host"


def advisory_key(name: str) -> int:
    """The signed int64 advisory key for a loop name (stable, derived)."""
    digest = hashlib.blake2b(
        f"{KEY_NAMESPACE}:{name}".encode("utf-8"), digest_size=8
    ).digest()
    return int.from_bytes(digest, "big", signed=True)


class LeaderLock:
    """One loop's leadership, held on its own connection.

    Not a context manager on purpose: leadership is held for the life of
    the process, across an arbitrary number of loop iterations, so the
    acquire and the release are on opposite sides of a wait rather than
    around a block.
    """

    def __init__(self, name: str, database_url: str | None = None) -> None:
        self.name = name
        self.key = advisory_key(name)
        if database_url is None:
            from resource_explorer.config import get_config

            database_url = get_config().registry.database_url
        self.database_url = database_url
        self.is_postgres = database_url.startswith("postgresql")
        self._conn = None
        self._engine = None
        self._held = False
        self._lock = threading.Lock()

    # ── state ───────────────────────────────────────────────────────────
    @property
    def held(self) -> bool:
        return self._held

    # ── acquire / release ───────────────────────────────────────────────
    def acquire(self) -> bool:
        """True if this process now holds the lock (or election does not
        apply). Never raises: an unreachable registry is 'not leader',
        and the caller retries at its own interval."""
        with self._lock:
            if self._held:
                return True
            if not self.is_postgres:
                log.warning(
                    "leader election is a no-op for a non-Postgres registry "
                    "(%s): loop %r will run unelected. The registry is "
                    "Postgres-only per docs/runtime-architecture-plan.md §3.",
                    self.database_url.split("://", 1)[0], self.name,
                )
                self._held = True
                _held_locks[self.name] = self
                return True
            try:
                from sqlalchemy import create_engine
                from sqlalchemy.pool import NullPool

                if self._engine is None:
                    self._engine = create_engine(
                        self.database_url, poolclass=NullPool
                    )
                conn = self._engine.raw_connection()
                # Autocommit, or this connection sits "idle in transaction"
                # for the life of the process — verified live 2026-09-04:
                # all three lock sessions showed that state in
                # pg_stat_activity. Advisory locks are session-scoped, so
                # they do not need the transaction; an open one just blocks
                # vacuum on a shared instance for hours. Set on the DBAPI
                # connection itself, since SQLAlchemy's raw_connection is a
                # proxy that does not forward the attribute.
                try:
                    (getattr(conn, "dbapi_connection", None) or conn).autocommit = True
                except (AttributeError, TypeError) as exc:
                    # Narrow on purpose: setting this attribute can only
                    # fail because the proxy shape changed, and anything
                    # wider here would swallow a real connection error
                    # that acquire()'s own handler should be reporting.
                    log.debug("could not set autocommit on the lock "
                              "connection for %r: %s", self.name, exc)
                cur = conn.cursor()
                cur.execute("SELECT pg_try_advisory_lock(%s)", (self.key,))
                won = bool(cur.fetchone()[0])
                cur.close()
                if won:
                    # Hold the connection: a session-scoped lock lives
                    # exactly as long as its session does.
                    self._conn = conn
                    self._held = True
                    _held_locks[self.name] = self
                else:
                    conn.close()
                return won
            except Exception as exc:
                log.warning(
                    "leader election for %r could not reach the registry "
                    "(%s: %s) — treating as 'not leader' and retrying",
                    self.name, type(exc).__name__, exc,
                )
                return False

    def release(self) -> None:
        """Give up leadership. Idempotent; never raises.

        Closing the connection would be enough for Postgres, but the
        explicit unlock makes the intent legible in ``pg_locks`` the
        instant it happens rather than whenever the socket teardown is
        noticed.
        """
        with self._lock:
            if not self._held:
                return
            self._held = False
            conn, self._conn = self._conn, None
            if conn is None:
                return  # non-Postgres no-op
            try:
                cur = conn.cursor()
                cur.execute("SELECT pg_advisory_unlock(%s)", (self.key,))
                cur.close()
            except Exception as exc:
                log.debug("advisory unlock for %r failed: %s", self.name, exc)
            try:
                conn.close()
            except Exception:
                pass
            if self._engine is not None:
                try:
                    self._engine.dispose()
                except Exception:
                    pass
                self._engine = None

    def still_held(self) -> bool:
        """Ask Postgres, not the cached flag: is this session still holding the key? A lost
        connection (which drops a session-scoped lock) or any error is False. A non-Postgres
        registry has no election, so it is True while held."""
        with self._lock:
            if not self._held:
                return False
            if self._conn is None:
                return True
            try:
                cur = self._conn.cursor()
                cur.execute(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted "
                    "AND pid = pg_backend_pid() AND classid::bigint = %s AND objid::bigint = %s",
                    ((self.key >> 32) & 0xFFFFFFFF, self.key & 0xFFFFFFFF),
                )
                held = bool(cur.fetchone()[0])
                cur.close()
                return held
            except Exception as exc:
                log.warning("leader check for %r failed (%s) — treating as not leader", self.name, exc)
                return False

    # Release on garbage collection too, so a dropped lock in a test does
    # not keep a key held until the interpreter exits.
    def __del__(self) -> None:  # pragma: no cover - GC timing
        try:
            self.release()
        except Exception:
            pass


#: Locks this process has won, by name (kept after a release, so "won then lost" is not
#: mistaken for "never elected").
_held_locks: dict[str, "LeaderLock"] = {}


def still_leader(name: str) -> bool | None:
    """Does this process still lead `name`? None when it never won that lock here (a manual run
    or a test: no election applies); otherwise `LeaderLock.still_held()`."""
    lock = _held_locks.get(name)
    if lock is None:
        return None
    return lock.still_held()


ALL_LOCK_NAMES = (LOCK_SCHEDULER, LOCK_BOOTSTRAP, LOCK_EGERIA_RESYNC, LOCK_DRAFT_ZONE, LOCK_ENGINE_HOST)


def documented_keys() -> dict[str, int]:
    """{loop name: advisory key} — what docs/process-model.md records."""
    return {name: advisory_key(name) for name in ALL_LOCK_NAMES}


if __name__ == "__main__":  # pragma: no cover
    for _name, _key in documented_keys().items():
        print(f"{_name:20s} {_key}")
