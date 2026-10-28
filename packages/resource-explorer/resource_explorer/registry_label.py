"""Credential-free description of which registry a process opened.

A worktree has no `.env`, and RE defaults its registry to the SHARED Postgres
(localhost:5442/egeria_advisor, schema resource_explorer). A builder command
that silently opens it migrates and writes the shared registry. This module
names the registry in one line so a stray open is visible in the log and on
the CLI. It never emits a username, password or the raw URL.
"""
from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlsplit

TEST_SCHEMA_PREFIX = "resource_explorer_test"


def _search_path(query: str) -> str | None:
    for opt in parse_qs(query).get("options", []):
        for part in unquote(opt).split():
            if part.startswith("-csearch_path="):
                return part.split("=", 1)[1].split(",")[0].strip().strip('"') or None
    return None


def _shared_default() -> tuple[str, int, str]:
    """(host, port, dbname) of the declared default registry, never the env."""
    from resource_explorer.config import RegistryConfig

    u = urlsplit(RegistryConfig.model_fields["database_url"].default)
    return (u.hostname or "localhost", u.port or 5432, (u.path or "/").lstrip("/"))


def is_shared_registry(database_url: str) -> bool:
    """True when the URL addresses the shared dev registry (not a test schema)."""
    if not database_url.startswith("postgresql"):
        return False
    try:
        u = urlsplit(database_url)
        host, port, db = u.hostname or "", u.port or 5432, (u.path or "/").lstrip("/")
    except ValueError:
        return False
    d_host, d_port, d_db = _shared_default()
    if host.lower() not in ("localhost", "127.0.0.1", "::1", d_host) or (port, db) != (d_port, d_db):
        return False
    schema = _search_path(u.query)
    return not (schema or "").startswith(TEST_SCHEMA_PREFIX)


def describe_registry(database_url: str) -> str:
    """`registry: ...` line: host and database only, never credentials."""
    if database_url.startswith("sqlite"):
        path = database_url.split("://", 1)[-1].split("?", 1)[0].lstrip("/")
        base = ":memory:" if ":memory:" in database_url else (path.rsplit("/", 1)[-1] or "?")
        return f"registry: sqlite:///{base}"
    try:
        u = urlsplit(database_url)
        host, port, db = u.hostname or "?", u.port, (u.path or "/").lstrip("/")
        schema = _search_path(u.query)
    except ValueError:
        return "registry: <unparseable url>"
    where = f"{host}:{port}" if port else host
    line = f"registry: {where}/{db}"
    if is_shared_registry(database_url):
        line += " (shared)"
    if schema:
        line += f" schema={schema}"
    return line
