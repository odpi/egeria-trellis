"""Connection configuration for PgVectorStore.

Deliberately a plain, frozen stdlib dataclass — NOT pydantic/BaseSettings —
and it never reads the environment or a YAML file itself. Resource Explorer
and Egeria Advisor each already have their own config system (RE: a single
live PgVectorConfig; EA: env settings + a YAML pgvector: block, with YAML
winning per-key — see advisor/vector_store.py's get_vector_store()) and each
keeps owning that resolution, building this dataclass as the last step
before constructing a PgVectorStore. Making this type read the environment
would make it a THIRD config surface racing EA's existing two, not a fix.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PgVectorStoreConfig:
    host: str
    port: int
    dbname: str
    # Named "user", not "db_user" — safe here because this dataclass never
    # reads the environment. RE's own config class deliberately avoids a
    # bare "user" field (resource_explorer/config.py's PgVectorConfig uses
    # db_user aliased to PGVECTOR_USER) specifically because pydantic-settings
    # would otherwise also accept the near-universal shell env var $USER as
    # an unintended default — that concern doesn't apply to a plain
    # dataclass. Don't "fix" this field name by converting this class to
    # BaseSettings later; that would reintroduce the exact collision RE's
    # config worked around.
    user: str
    password: str
    # None => tables are unqualified (relies on Postgres search_path,
    # effectively "public" in both apps' deployments today) — this is EA's
    # current behavior, reproduced exactly, not "fixed" to be schema-
    # qualified. A non-None value => every identifier is schema-qualified
    # ("{schema}"."{table}") and information_schema lookups filter on it.
    # This is RE's current behavior (schema="resource_explorer").
    schema: str | None = None
    max_connections: int = 10
    ef_search: int = 100
    embedding_dim: int = 384
    # Seconds before psycopg2.connect() (both the pool's own connections and
    # the bootstrap extension/schema connection in connect()) gives up and
    # raises OperationalError, instead of blocking the calling thread
    # indefinitely. Added 2026-09-13 after a live incident (RE's :8810 web
    # process) where ThreadedConnectionPool's underlying connect() hung with
    # Postgres itself healthy and reachable from every other process — see
    # pg.py's connect() docstring for what None means here.
    connect_timeout: int = 10
    # libpq connection options sent on EVERY connection this store opens (the pool's and the bootstrap's), e.g.
    # "-c search_path=public". None sends none. EA pins its search_path here (TR-60a): with schema=None its tables are
    # unqualified, and a role whose name matches a schema ("$user", public) silently read that schema's empty copies.
    options: str | None = None
