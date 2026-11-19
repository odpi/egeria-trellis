"""Tests for the shared QueryCache.

The LRU-vs-FIFO tests below are the point of the extraction: Egeria Advisor's
cache would have failed `test_access_promotes_an_entry_past_a_newer_one`,
which is exactly the bug the audit found.
"""
from __future__ import annotations

import sys
import time
import types

import pytest

from trellis_querycache import CacheEntry, QueryCache, QueryCacheConfig


@pytest.fixture
def cache():
    return QueryCache(QueryCacheConfig(max_size=5, ttl_seconds=60))


class TestConfig:
    def test_rejects_a_zero_max_size(self):
        with pytest.raises(ValueError, match="max_size"):
            QueryCacheConfig(max_size=0)

    def test_rejects_a_nonpositive_ttl(self):
        with pytest.raises(ValueError, match="ttl_seconds"):
            QueryCacheConfig(ttl_seconds=0)

    def test_allows_a_none_ttl_meaning_never_expires(self):
        assert QueryCacheConfig(ttl_seconds=None).ttl_seconds is None

    def test_rejects_an_unknown_backend(self):
        with pytest.raises(ValueError, match="backend"):
            QueryCacheConfig(backend="memcached")

    def test_redis_backend_requires_a_url(self):
        with pytest.raises(ValueError, match="redis_url"):
            QueryCacheConfig(backend="redis")


class TestBasicOperations:
    def test_miss_returns_none(self, cache):
        assert cache.get("q") is None

    def test_set_and_get(self, cache):
        cache.set("what is X", "X is a thing")
        assert cache.get("what is X") == "X is a thing"

    def test_query_is_normalized_for_case_and_whitespace(self, cache):
        cache.set("  What Is X  ", "X is a thing")
        assert cache.get("what is x") == "X is a thing"

    def test_scopes_are_separate(self, cache):
        cache.set("q", "answer A", scope="a")
        cache.set("q", "answer B", scope="b")
        assert cache.get("q", scope="a") == "answer A"
        assert cache.get("q", scope="b") == "answer B"
        assert cache.get("q") is None

    def test_params_are_part_of_the_key(self, cache):
        cache.set("q", "general answer", intent="general")
        cache.set("q", "stats answer", intent="statistical")
        assert cache.get("q", intent="general") == "general answer"
        assert cache.get("q", intent="statistical") == "stats answer"

    def test_none_valued_params_do_not_affect_the_key(self, cache):
        cache.set("q", "answer", intent="general")
        assert cache.get("q", intent="general", top_k=None) == "answer"

    def test_param_order_does_not_affect_the_key(self, cache):
        cache.set("q", "answer", intent="general", top_k=5)
        assert cache.get("q", top_k=5, intent="general") == "answer"

    def test_holds_arbitrary_objects_not_just_strings(self, cache):
        payload = {"chunks": [1, 2, 3], "obj": object()}
        cache.set("q", payload)
        assert cache.get("q") is payload

    def test_overwriting_a_key_replaces_the_value(self, cache):
        cache.set("q", "old")
        cache.set("q", "new")
        assert cache.get("q") == "new"


class TestTTLExpiry:
    def test_an_expired_entry_reads_as_a_miss(self, cache):
        cache.set("q", "answer")
        entry = cache._store[cache.key("q")]
        entry.expires_at = time.time() - 1
        assert cache.get("q") is None

    def test_an_expired_entry_is_dropped_not_just_hidden(self, cache):
        cache.set("q", "answer")
        cache._store[cache.key("q")].expires_at = time.time() - 1
        cache.get("q")
        assert cache._store == {}

    def test_a_none_ttl_never_expires(self):
        cache = QueryCache(QueryCacheConfig(max_size=5, ttl_seconds=None))
        cache.set("q", "answer")
        assert cache._store[cache.key("q")].expires_at is None
        assert cache.get("q") == "answer"

    def test_re_setting_a_key_refreshes_its_expiry(self, cache):
        cache.set("q", "answer")
        cache._store[cache.key("q")].expires_at = time.time() - 1
        cache.set("q", "answer")
        assert cache.get("q") == "answer"


class TestLRUEviction:
    def test_evicts_the_least_recently_used_entry_when_full(self):
        cache = QueryCache(QueryCacheConfig(max_size=3, ttl_seconds=3600))
        for q in ("q1", "q2", "q3"):
            cache.set(q, q)
        cache.get("q1")           # q1 is now the most recently used
        cache.set("q4", "q4")     # so q2 is the one that should go
        assert cache.get("q2") is None
        assert cache.get("q1") == "q1"
        assert cache.get("q3") == "q3"
        assert cache.get("q4") == "q4"

    def test_access_promotes_an_entry_past_a_newer_one(self):
        """The regression EA's FIFO cache shipped: a hot entry evicted while a
        colder, newer one survived."""
        cache = QueryCache(QueryCacheConfig(max_size=2, ttl_seconds=3600))
        cache.set("hot", "a")
        cache.set("cold", "b")
        for _ in range(10):
            cache.get("hot")
        cache.set("new", "c")
        assert cache.get("hot") == "a"     # FIFO would have evicted this
        assert cache.get("cold") is None

    def test_never_grows_past_max_size(self):
        cache = QueryCache(QueryCacheConfig(max_size=3, ttl_seconds=3600))
        for i in range(50):
            cache.set(f"q{i}", i)
        assert len(cache._store) == 3


class TestScopeInvalidation:
    def test_invalidate_scope_removes_only_that_scope(self, cache):
        cache.set("q1", "a1", scope="mine", intent="general")
        cache.set("q2", "a2", scope="mine", intent="statistical")
        cache.set("q3", "a3", scope="other", intent="general")
        assert cache.invalidate_scope("mine") == 2
        assert cache.get("q1", scope="mine", intent="general") is None
        assert cache.get("q2", scope="mine", intent="statistical") is None
        assert cache.get("q3", scope="other", intent="general") == "a3"

    def test_invalidating_an_unknown_scope_returns_zero(self, cache):
        assert cache.invalidate_scope("ghost") == 0

    def test_unscoped_entries_survive_invalidation(self, cache):
        cache.set("q", "a")
        cache.invalidate_scope("anything")
        assert cache.get("q") == "a"


class TestStats:
    def test_counts_hits_and_misses(self, cache):
        cache.set("q", "a")
        cache.get("q")
        cache.get("q")
        cache.get("absent")
        stats = cache.get_stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["total_requests"] == 3
        assert stats["hit_rate"] == pytest.approx(200 / 3)

    def test_hit_rate_is_zero_with_no_requests(self, cache):
        assert cache.get_stats()["hit_rate"] == 0.0

    def test_an_expired_read_counts_as_a_miss(self, cache):
        cache.set("q", "a")
        cache._store[cache.key("q")].expires_at = time.time() - 1
        cache.get("q")
        assert cache.get_stats()["misses"] == 1

    def test_most_popular_ranks_by_hit_count(self, cache):
        cache.set("hot", "a")
        cache.set("warm", "b")
        for _ in range(5):
            cache.get("hot")
        cache.get("warm")
        popular = cache.most_popular(2)
        assert [p["hits"] for p in popular] == [5, 1]

    def test_clear_empties_the_store_and_the_counters(self, cache):
        cache.set("q", "a")
        cache.get("q")
        cache.clear()
        assert cache.get_stats()["size"] == 0
        assert cache.get_stats()["hits"] == 0
        assert cache.get("q") is None


class _FakeRedisError(Exception):
    """Stands in for `redis.exceptions.RedisError`."""


class _FakeRedis:
    """The handful of Redis commands the backend uses, in memory, with an
    `up` switch that makes every command fail the way a lost server does."""

    def __init__(self) -> None:
        self.up = True
        self.data: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    def _check(self) -> None:
        if not self.up:
            raise _FakeRedisError("Error 111 connecting to localhost:6379. Connection refused.")

    def get(self, key):
        self._check()
        return self.data.get(key)

    def set(self, key, value):
        self._check()
        self.data[key] = value

    def setex(self, key, ttl, value):
        self._check()
        self.data[key] = value

    def sadd(self, key, member):
        self._check()
        self.sets.setdefault(key, set()).add(member)

    def expire(self, key, ttl):
        self._check()

    def smembers(self, key):
        self._check()
        return set(self.sets.get(key, set()))

    def delete(self, *keys):
        self._check()
        gone = 0
        for k in keys:
            gone += (self.data.pop(k, None) is not None) + (self.sets.pop(k, None) is not None)
        return gone

    def scan_iter(self, match, count):
        self._check()
        prefix = match.rstrip("*")
        return [k for k in list(self.data) if k.startswith(prefix)]


@pytest.fixture
def fake_redis(monkeypatch):
    """Install a fake `redis` module so the backend runs without a server or
    the optional `redis` extra."""
    server = _FakeRedis()
    module = types.ModuleType("redis")
    module.from_url = lambda url, decode_responses: server
    module.exceptions = types.SimpleNamespace(RedisError=_FakeRedisError)
    monkeypatch.setitem(sys.modules, "redis", module)
    return server


@pytest.fixture
def redis_cache(fake_redis):
    return QueryCache(QueryCacheConfig(ttl_seconds=60, backend="redis",
                                       redis_url="redis://localhost:6379/0"))


class TestRedisBackend:
    def test_set_and_get(self, redis_cache):
        redis_cache.set("q", {"answer": 42}, scope="r", intent="general")
        assert redis_cache.get("q", scope="r", intent="general") == {"answer": 42}

    def test_invalidate_scope_removes_only_that_scope(self, redis_cache):
        redis_cache.set("q", "a", scope="r1")
        redis_cache.set("q", "b", scope="r2")
        assert redis_cache.invalidate_scope("r1") == 1
        assert redis_cache.get("q", scope="r1") is None
        assert redis_cache.get("q", scope="r2") == "b"

    def test_a_non_json_value_is_left_uncached(self, redis_cache, fake_redis):
        redis_cache.set("q", object())
        assert fake_redis.data == {}


class TestRedisUnavailable:
    """A cache that cannot reach its store must not break the operation it was
    speeding up: reads are misses, writes are skipped, nothing raises."""

    def test_a_read_is_a_miss_not_an_error(self, redis_cache, fake_redis):
        redis_cache.set("q", "a")
        fake_redis.up = False
        assert redis_cache.get("q") is None
        assert redis_cache.get_stats()["misses"] == 1

    def test_a_write_is_skipped_not_an_error(self, redis_cache, fake_redis):
        fake_redis.up = False
        redis_cache.set("q", "a", scope="r")
        fake_redis.up = True
        assert redis_cache.get("q", scope="r") is None

    def test_a_write_that_fails_part_way_leaves_no_untracked_entry(self, redis_cache, fake_redis):
        """Redis dropping between the scope-set and value commands must not
        leave an entry invalidate_scope cannot find."""
        def lost(*args):
            raise _FakeRedisError("Connection reset by peer")

        fake_redis.sadd = lost
        redis_cache.set("q", "a", scope="r")
        del fake_redis.sadd
        assert redis_cache.get("q", scope="r") is None

    def test_the_cache_works_again_once_redis_is_back(self, redis_cache, fake_redis):
        fake_redis.up = False
        assert redis_cache.get("q") is None
        fake_redis.up = True
        redis_cache.set("q", "a")
        assert redis_cache.get("q") == "a"

    def test_the_failure_is_logged(self, redis_cache, fake_redis, caplog):
        fake_redis.up = False
        with caplog.at_level("WARNING", logger="trellis_querycache.cache"):
            redis_cache.get("q")
            redis_cache.set("q", "a")
        assert "read failed" in caplog.text
        assert "write failed" in caplog.text

    def test_invalidation_still_raises(self, redis_cache, fake_redis):
        """Reporting an invalidation that never reached Redis as done would
        leave stale answers to be served once it is back."""
        fake_redis.up = False
        with pytest.raises(_FakeRedisError):
            redis_cache.invalidate_scope("r")

    def test_errors_other_than_redis_errors_still_raise(self, redis_cache, fake_redis):
        """Only an unavailable store is absorbed; a bug in the cache is not."""
        def broken(key):
            raise AttributeError("not a Redis failure")

        fake_redis.get = broken
        with pytest.raises(AttributeError):
            redis_cache.get("q")


class TestCacheEntry:
    def test_a_none_expiry_is_never_expired(self):
        assert not CacheEntry(value="v", scope=None, expires_at=None).is_expired()

    def test_a_past_expiry_is_expired(self):
        assert CacheEntry(value="v", scope=None, expires_at=time.time() - 1).is_expired()
