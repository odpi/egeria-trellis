"""TTL + LRU query cache, in-process by default and Redis-backed optionally.

Extracted from two independent implementations (see
`docs/re-ea-consolidation-audit.md` item 1):

* Resource Explorer's was a genuine LRU — `OrderedDict` with `move_to_end()`
  on access — with per-entry TTL, an optional Redis backend, and scope
  invalidation. That is the base here.
* Egeria Advisor's was named and documented as LRU throughout but was a plain
  `dict` that evicted the oldest *insertion*, never reordering on access. A
  frequently-hit entry was evicted while a cold one inserted later survived.
  Only its hit/miss/`most_popular` telemetry was worth keeping, and it is
  layered on top here rather than lost.

Vocabulary note: the invalidation namespace is called a **scope**, not a
project or a resource. This class has no opinion about what the caller is
scoping by, which also keeps it clear of the `project_slug` -> `resource_slug`
rename in flight elsewhere in the repo. Each app's adapter names it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from trellis_querycache.config import QueryCacheConfig

log = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """One cached value plus what LRU, TTL, invalidation and stats each need."""

    value: Any
    scope: str | None
    expires_at: float | None  # None => never expires
    hit_count: int = 0

    def is_expired(self, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now if now is not None else time.time()) > self.expires_at


class QueryCache:
    """Cache keyed on a normalized query plus an arbitrary set of parameters.

    Values may be any Python object under the in-memory backend. The Redis
    backend can only hold JSON-serializable values; a value that is not is
    logged and left uncached rather than raising into the caller's request
    path — a cache that cannot store something must still not break the
    operation it was speeding up.

    The same rule covers an unavailable Redis. When the server is down,
    unreachable or refuses a command, `get` reads as a miss and `set` stores
    nothing, both with a logged warning, so the caller recomputes instead of
    failing. `invalidate_scope` and `clear` still raise: they are explicit
    maintenance calls, and reporting success for an invalidation that never
    reached Redis would leave stale answers to be served once it is back.
    """

    def __init__(self, config: QueryCacheConfig | None = None) -> None:
        self._config = config or QueryCacheConfig()
        self._hits = 0
        self._misses = 0
        # Exceptions that mean "Redis is unavailable" — set by _connect_redis,
        # because the redis package is only importable when it is configured.
        self._redis_errors: tuple[type[Exception], ...] = ()

        if self._config.uses_redis:
            self._redis = self._connect_redis(self._config.redis_url)
            self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        else:
            self._redis = None
            self._store = OrderedDict()

    # ── keys ──────────────────────────────────────────────────────────────

    def key(self, query: str, scope: str | None = None, params: dict[str, Any] | None = None) -> str:
        """Deterministic cache key. Public because callers legitimately want to
        pre-compute or log one; the digest itself is an implementation detail."""
        payload = json.dumps(
            {
                "q": query.strip().lower(),
                "s": scope,
                "p": {k: v for k, v in sorted((params or {}).items()) if v is not None},
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def _expiry(self) -> float | None:
        ttl = self._config.ttl_seconds
        return None if ttl is None else time.time() + ttl

    # ── public interface ──────────────────────────────────────────────────

    def get(self, query: str, *, scope: str | None = None, **params: Any) -> Any | None:
        """Return the cached value, or None on a miss or an expired entry."""
        key = self.key(query, scope, params)
        value = self._redis_get(key) if self._redis is not None else self._mem_get(key)
        if value is None:
            self._misses += 1
            return None
        self._hits += 1
        return value

    def set(self, query: str, value: Any, *, scope: str | None = None, **params: Any) -> None:
        """Cache `value`. Overwriting an existing key refreshes its TTL and
        marks it most-recently-used."""
        key = self.key(query, scope, params)
        if self._redis is not None:
            self._redis_set(key, value, scope)
        else:
            self._mem_set(key, value, scope)

    def invalidate_scope(self, scope: str) -> int:
        """Drop every entry cached under `scope`; returns how many went.

        Call after anything that changes what a query would answer for that
        scope — a re-index, a re-ingest, a survey that rewrote findings.
        """
        if self._redis is not None:
            return self._redis_invalidate(scope)
        return self._mem_invalidate(scope)

    def clear(self) -> None:
        """Empty the cache and reset the hit/miss counters."""
        self._store.clear()
        if self._redis is not None:
            self._redis_clear()
        self._hits = 0
        self._misses = 0
        log.info("Query cache cleared")

    # ── telemetry (from Egeria Advisor's implementation) ──────────────────

    def get_stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        return {
            "size": len(self._store),
            "max_size": self._config.max_size,
            "backend": self._config.backend,
            "hits": self._hits,
            "misses": self._misses,
            "total_requests": total,
            "hit_rate": (self._hits / total * 100) if total else 0.0,
            "most_popular": self.most_popular(5),
        }

    def most_popular(self, n: int = 5) -> list[dict[str, Any]]:
        """The n most-hit entries currently held.

        Only ever reflects the in-memory store: under the Redis backend the
        entries live in Redis and per-entry hit counts are not tracked there,
        so this is honestly empty rather than misleadingly partial.
        """
        ranked = sorted(self._store.items(), key=lambda kv: kv[1].hit_count, reverse=True)
        return [{"hash": k[:8], "hits": e.hit_count, "scope": e.scope} for k, e in ranked[:n]]

    # ── in-memory backend ─────────────────────────────────────────────────

    def _mem_get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if entry.is_expired():
            del self._store[key]
            return None
        entry.hit_count += 1
        self._store.move_to_end(key)  # the half EA's implementation was missing
        return entry.value

    def _mem_set(self, key: str, value: Any, scope: str | None) -> None:
        self._store[key] = CacheEntry(value=value, scope=scope, expires_at=self._expiry())
        self._store.move_to_end(key)
        while len(self._store) > self._config.max_size:
            self._store.popitem(last=False)

    def _mem_invalidate(self, scope: str) -> int:
        doomed = [k for k, e in self._store.items() if e.scope == scope]
        for k in doomed:
            del self._store[k]
        return len(doomed)

    # ── Redis backend ─────────────────────────────────────────────────────

    def _connect_redis(self, url: str):
        import redis  # imported lazily: only apps that configure Redis need it

        self._redis_errors = (redis.exceptions.RedisError,)
        return redis.from_url(url, decode_responses=True)

    def _scope_set_key(self, scope: str) -> str:
        return f"{self._config.scope_prefix}{scope}:keys"

    def _redis_get(self, key: str) -> Any | None:
        try:
            raw = self._redis.get(self._config.key_prefix + key)
        except self._redis_errors as exc:
            # A cache outage is a miss, not a failed request.
            log.warning("Redis cache read failed; treating as a miss: %s", exc)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)["value"]
        except (ValueError, KeyError, TypeError):
            log.warning("Discarding unreadable Redis cache entry %s", key[:8])
            return None

    def _redis_set(self, key: str, value: Any, scope: str | None) -> None:
        try:
            payload = json.dumps({"value": value, "scope": scope})
        except (TypeError, ValueError):
            # Not cacheable over Redis. Skip it — never fail the caller's request
            # for the sake of an optimisation.
            log.debug("Value for %s is not JSON-serializable; not cached", key[:8])
            return

        try:
            self._redis_write(self._config.key_prefix + key, payload, scope)
        except self._redis_errors as exc:
            # Same rule as an unserializable value: leave it uncached.
            log.warning("Redis cache write failed; value not cached: %s", exc)

    def _redis_write(self, redis_key: str, payload: str, scope: str | None) -> None:
        # Track the key under its scope BEFORE writing the value. If Redis fails
        # between the two commands, a set member with no entry is harmless; an
        # entry its scope does not list would survive invalidate_scope and keep
        # serving a stale answer.
        if scope is not None:
            scope_set = self._scope_set_key(scope)
            self._redis.sadd(scope_set, redis_key)
            if self._config.ttl_seconds is not None:
                # Outlive the entries it tracks, so invalidation still finds them.
                self._redis.expire(scope_set, int(self._config.ttl_seconds) * 2)

        # Redis has no "never expires" TTL via setex, so fall back to a plain
        # set when the config says entries do not expire.
        if self._config.ttl_seconds is None:
            self._redis.set(redis_key, payload)
        else:
            self._redis.setex(redis_key, int(self._config.ttl_seconds), payload)

    def _redis_invalidate(self, scope: str) -> int:
        scope_set = self._scope_set_key(scope)
        keys = self._redis.smembers(scope_set)
        if not keys:
            return 0
        deleted = self._redis.delete(*keys)
        self._redis.delete(scope_set)
        return deleted

    def _redis_clear(self) -> None:
        """Delete only this cache's own keys — never `FLUSHDB`, which would take
        out whatever else shares the Redis instance."""
        pattern = self._config.key_prefix + "*"
        batch: list[str] = []
        for key in self._redis.scan_iter(match=pattern, count=500):
            batch.append(key)
            if len(batch) >= 500:
                self._redis.delete(*batch)
                batch = []
        if batch:
            self._redis.delete(*batch)
