"""Under pytest-xdist, each worker gets its own registry schema.

CI points REGISTRY/FEEDBACK/METRICS_DATABASE_URL at its service container's
`resource_explorer` schema, and `isolate_url_settings` deliberately leaves them
there, so a serial CI run shares that one schema across the whole run. With
several workers it would also be shared by tests running at the same moment: a
race serial CI never had. So in every xdist worker a URL whose search_path is
`resource_explorer` is pointed at `resource_explorer_xdist_<run>_<worker>`, where
<run> is the controller's pid. Two concurrent runs on one Postgres therefore
never share a worker schema, and every worker sees what a serial run over its
share of the tests would.

The schema is created (the feedback and metrics stores do not create one) and
dropped at the worker's session teardown only where the URL is actually USED:
in CI, or wherever `isolate_url_settings` would leave it alone (a non-shared
Postgres). Against the shared dev registry outside CI the URL is replaced per
test by a temp SQLite file anyway, so nothing is created there: a local gate-mode
run does no DDL on the shared instance for this. To exercise the Postgres path
locally, run CI mode (GITHUB_ACTIONS=true) against a private Postgres.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Mapping

SETTINGS = ("REGISTRY_DATABASE_URL", "FEEDBACK_DATABASE_URL", "METRICS_DATABASE_URL")
_SUFFIX = "search_path%3Dresource_explorer"
PREFIX = "resource_explorer_xdist"


def worker_schema(worker: str, run_id: str) -> str:
    return f"{PREFIX}_{run_id}_{worker}"


def rewritten(environ: Mapping[str, str], schema: str) -> dict[str, str]:
    """{setting: new URL} for each setting whose search_path is exactly `resource_explorer`. Pure."""
    out = {}
    for name in SETTINGS:
        url = environ.get(name, "")
        if url.endswith(_SUFFIX):
            out[name] = url[: -len("resource_explorer")] + schema
    return out


def is_used(environ: Mapping[str, str], url: str) -> bool:
    """True when tests will really connect to `url`: isolate_url_settings leaves it alone."""
    if environ.get("GITHUB_ACTIONS", "").lower() == "true":
        return True
    from resource_explorer.registry_label import is_shared_registry

    return not is_shared_registry(url)


def _server_dsn(url: str) -> str:
    return url.split("?", 1)[0]


def create(url: str, schema: str) -> None:
    import psycopg2

    conn = psycopg2.connect(_server_dsn(url), connect_timeout=5)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    finally:
        conn.close()


def drop(url: str, schema: str) -> None:
    import psycopg2

    conn = psycopg2.connect(_server_dsn(url), connect_timeout=5)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SET lock_timeout = '15s'")   # fail the teardown rather than hang it
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    finally:
        conn.close()


def apply(environ: dict, worker: str, run_id: str,
          create_fn: Callable[[str, str], None] = create) -> dict[str, Any] | None:
    """Rewrite `environ` for this worker; create the schema where it is used. Returns what teardown needs."""
    if not worker:
        return None
    schema = worker_schema(worker, run_id)
    urls = rewritten(environ, schema)
    if not urls:
        return None
    environ.update(urls)
    used = sorted({_server_dsn(u) for u in urls.values() if is_used(environ, u)})
    created = []
    for dsn in used:
        try:
            create_fn(dsn, schema)
            created.append(dsn)
        except Exception:  # noqa: BLE001 - unreachable is RE_REQUIRE_POSTGRES's to report
            pass
    return {"schema": schema, "urls": urls, "created": created}


def teardown(info: dict[str, Any] | None, drop_fn: Callable[[str, str], None] = drop) -> list[str]:
    """Drop the worker schema wherever apply() created it. Returns the servers it was dropped on."""
    if not info or not info["created"]:
        return []
    from resource_explorer.registry import ProjectRegistry

    # Pooled connections on the schema would hold locks DROP SCHEMA waits for.
    for url in info["urls"].values():
        engine = ProjectRegistry._pg_engine_cache.pop(url, None)
        if engine is not None:
            engine.dispose()
        ProjectRegistry._pg_schema_ready.discard(url)
    dropped = []
    for dsn in info["created"]:
        drop_fn(dsn, info["schema"])
        dropped.append(dsn)
    return dropped


def run_id() -> str:
    """The controller's pid: an xdist worker is a direct child of the controller."""
    return str(os.getppid())
