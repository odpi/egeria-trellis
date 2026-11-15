# trellis-querycache

A TTL + LRU query cache shared by Resource Explorer and Egeria Advisor —
in-process by default, Redis-backed optionally.

## Why this package exists

Both apps grew a `query_cache.py` with the same purpose and different
behaviour (`docs/re-ea-consolidation-audit.md`, item 1):

| | Resource Explorer | Egeria Advisor |
|---|---|---|
| Eviction | genuine LRU (`OrderedDict` + `move_to_end()` on access) | **FIFO** — plain `dict`, never reordered, despite being named and documented as LRU throughout |
| TTL | per-entry, lazily evicted | none |
| Redis backend | yes, with scope invalidation | no |
| Hit/miss telemetry | none | yes, including `most_popular` |

RE's implementation is the base; EA's telemetry is layered on top. **EA's
eviction bug is fixed as a side effect of adopting the shared class, not as
separate work** — a hot entry there was evicted while a colder, newer one
survived. `tests/test_cache.py::TestLRUEviction::test_access_promotes_an_entry_past_a_newer_one`
is the regression test for exactly that.

## Usage

```python
from trellis_querycache import QueryCache, QueryCacheConfig

cache = QueryCache(QueryCacheConfig(max_size=500, ttl_seconds=600))

cache.set("what is X", answer, scope="my-repo", intent="general")
cache.get("what is X", scope="my-repo", intent="general")
cache.invalidate_scope("my-repo")   # after a re-index/re-ingest
cache.get_stats()
```

Apps do not use this class directly — each keeps a thin adapter that names the
scope in its own vocabulary (`resource_explorer/query_cache.py`,
`advisor/query_cache.py`), the same shape `trellis-vectorstore` uses.

## Design notes

**Scope, not project.** The invalidation namespace is a *scope*. This class has
no opinion about what the caller scopes by, which also keeps it clear of the
`project_slug` → `resource_slug` rename in flight elsewhere in the repo.

**No dependencies.** No pydantic (config is a frozen dataclass; each app
resolves its own environment into one), no loguru (stdlib `logging`), and
`redis` is imported lazily and only when an app configures it.

**Values.** Any Python object under the in-memory backend. The Redis backend
can only hold JSON-serializable values; anything else is logged and left
uncached rather than raised — a cache that cannot store something must not
break the operation it was speeding up.

**An unavailable Redis is a miss, not an error.** When Redis is down,
unreachable or refuses a command, `get` returns `None` (counted as a miss) and
`set` stores nothing, each with a logged warning, so the caller recomputes the
answer instead of failing the request. `invalidate_scope()` and `clear()` still
raise: they are explicit maintenance calls, and reporting an invalidation that
never reached Redis as done would leave stale answers to be served once it is
back.

**`ttl_seconds=None` means never expires** — entries leave only by LRU
eviction or explicit invalidation. That is EA's historical behaviour, kept
rather than silently given a TTL.

**`clear()` never issues `FLUSHDB`** — it scans and deletes only this cache's
own prefixed keys, so it cannot take out whatever else shares the Redis
instance.
