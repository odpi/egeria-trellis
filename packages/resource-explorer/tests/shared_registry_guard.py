"""Fail-closed guard: no pytest test may connect to the SHARED registry.

Incident 2026-10-02: a new test set REGISTRY_DATABASE_URL (a temp SQLite file)
in a fixture, but `resource_explorer.config.get_config()` had already been
cached, so the env var was ignored. The test opened the shared development
Postgres (localhost:5442, database `egeria_advisor`, schema `resource_explorer`),
ran schema init and INSERTED two fake rows into the real `databases` table,
which broke `list_databases()` for the whole app until they were deleted by
hand.

The guard sits at the connection itself, not at config resolution, so a cached
config, a hard-coded URL, an engine cache or a forgotten fixture cannot route
around it:

* SQLAlchemy `do_connect` event, registered on the `Engine` class. Every engine
  in the process (registry, feedback, metrics collector, leader election,
  Prefect flows) funnels through it, immediately before the DBAPI connect.
* `psycopg2.connect` wrapper, for the code and fixtures that connect directly.

What counts as the shared registry
----------------------------------
Derived from the config classes' declared DEFAULTS (never from the cached
config or the environment, which is exactly what failed): a connection to the
dev instance host (localhost / loopback), port (5442) and database
(`egeria_advisor`) whose `search_path` is anything other than a throwaway test
schema (`resource_explorer_test*`, the prefix conftest's per-process
schemas use). On the SQLAlchemy path a URL with NO `search_path` option also
counts as shared: the registry's own default is the real schema, and the
public schema of the shared database is not a test's to write to.

Raw `psycopg2.connect` calls without a `search_path` option are NOT refused:
conftest's reachability probe and its `pg_test_schema` fixture legitimately
connect that way to create and drop the throwaway schema, and the corpus tests
read through it. Those cannot be classified by DSN alone; the registry path,
where the damage happened, is strict.

How to opt in: `RE_TESTS_ALLOW_SHARED_REGISTRY=1`. Nobody sets it by default.
CI is exempt without it (GITHUB_ACTIONS=true): its service container listens on
the same localhost:5442 / egeria_advisor / resource_explorer coordinates as the
dev instance but is created empty for the run and discarded after it.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

TEST_SCHEMA_PREFIX = "resource_explorer_test"
ALLOW_ENV = "RE_TESTS_ALLOW_SHARED_REGISTRY"
_LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1", "0.0.0.0"}


class SharedRegistryAccessError(RuntimeError):
    """A test tried to open a connection to the shared development registry."""


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() not in ("", "0", "false", "no")


def shared_instances() -> set[tuple[int, str]]:
    """(port, dbname) pairs of the dev instance, from declared defaults."""
    from resource_explorer.config import PgVectorConfig, RegistryConfig

    pairs = {(int(PgVectorConfig.model_fields["port"].default),
              str(PgVectorConfig.model_fields["dbname"].default))}
    url = urlsplit(RegistryConfig.model_fields["database_url"].default)
    pairs.add((url.port or 5432, (url.path or "/").lstrip("/")))
    return pairs


def _search_path(options: Any) -> str | None:
    """Schema named by libpq `options=-csearch_path=<name>`, else None."""
    if not options:
        return None
    for part in unquote(str(options)).split():
        if part.startswith("-csearch_path="):
            return part.split("=", 1)[1].split(",")[0].strip().strip('"') or None
    return None


def _params_from_args(args: tuple, kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise psycopg2.connect(dsn, **kw) / SQLAlchemy cparams to a dict."""
    params: dict[str, Any] = {}
    dsn = args[0] if args else kwargs.get("dsn")
    if isinstance(dsn, str) and dsn:
        if "://" in dsn:
            u = urlsplit(dsn)
            params.update(host=u.hostname, port=u.port, dbname=(u.path or "").lstrip("/"))
            opts = parse_qs(u.query).get("options")
            if opts:
                params["options"] = opts[0]
        else:
            try:
                from psycopg2.extensions import parse_dsn
                params.update(parse_dsn(dsn))
            except Exception:
                pass
    params.update({k: v for k, v in kwargs.items() if k != "dsn"})
    if "database" in params and "dbname" not in params:
        params["dbname"] = params["database"]
    return params


def classify(params: Mapping[str, Any], *, strict: bool) -> str | None:
    """Return a human reason if `params` address the shared registry, else None."""
    host = str(params.get("host") or "").strip().lower()
    try:
        port = int(params.get("port") or 5432)
    except (TypeError, ValueError):
        return None
    dbname = str(params.get("dbname") or "")
    if host not in _LOCAL_HOSTS or (port, dbname) not in shared_instances():
        return None
    schema = _search_path(params.get("options"))
    if schema is not None and schema.startswith(TEST_SCHEMA_PREFIX):
        return None
    if schema is None and not strict:
        return None
    where = f"schema {schema!r}" if schema else "no search_path (public)"
    return f"{host or 'localhost'}:{port}/{dbname}, {where}"


_SETTING_BY_MODULE = (
    ("metrics_collector", "METRICS_DATABASE_URL (observability.metrics_database_url)"),
    ("feedback_store", "FEEDBACK_DATABASE_URL (feedback.database_url)"),
    ("leader_election", "REGISTRY_DATABASE_URL (registry.database_url; leader election)"),
    ("registry", "REGISTRY_DATABASE_URL (registry.database_url)"),
)


def setting_hint(frames: list[str] | None = None) -> str:
    """Name the config setting behind a connection, from the calling modules.

    The three Postgres URL settings share one default, so the address cannot
    say which one a test forgot to override; the code that opened the engine
    can. `frames` is for tests; by default the live stack is read.
    """
    if frames is None:
        import traceback
        frames = [f.filename for f in traceback.extract_stack()]
    for name in reversed(frames):
        base = os.path.basename(name)
        for key, label in _SETTING_BY_MODULE:
            if base.startswith(key) and "resource_explorer" in name:
                return label
    return "unknown (REGISTRY_DATABASE_URL, METRICS_DATABASE_URL or FEEDBACK_DATABASE_URL)"


def check(params: Mapping[str, Any], *, strict: bool, test: str | None = None) -> None:
    """Raise SharedRegistryAccessError if the connection must be refused."""
    if _truthy(os.environ.get(ALLOW_ENV)):
        return
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        return
    reason = classify(params, strict=strict)
    if reason is None:
        return
    test = test or os.environ.get("PYTEST_CURRENT_TEST") or "<collection / session setup>"
    raise SharedRegistryAccessError(
        f"REFUSED before connecting: test {test} tried to open the SHARED "
        f"Resource Explorer database ({reason}) through setting "
        f"{setting_hint()}. Point that setting at a temp SQLite file "
        f"(monkeypatch.setenv, then reset resource_explorer.config._config). A test must use a temp SQLite "
        f"registry (sqlite:///tmp_path/...), a per-test Postgres schema named "
        f"{TEST_SCHEMA_PREFIX}_* (the pg_registry fixture), or pass an explicit "
        f"ProjectRegistry(database_url=...). If the config is cached "
        f"(resource_explorer.config._config), an env var set in a fixture is "
        f"ignored; conftest resets it between tests, but a URL baked in "
        f"elsewhere still lands here. To deliberately run against the shared "
        f"registry set {ALLOW_ENV}=1 (nobody does by default; coordinate with "
        f"live peers first)."
    )


# --- vector store (PGVECTOR_SCHEMA) -------------------------------------------

VECTOR_SCHEMA_ENV = "PGVECTOR_SCHEMA"


def shared_vector_schema() -> str:
    """The shared vector-store schema name, from PgVectorConfig's declared default."""
    from resource_explorer.config import PgVectorConfig

    return str(PgVectorConfig.model_fields["schema_name"].default)


def check_vector_schema(schema: str | None, *, test: str | None = None) -> None:
    """Refuse a PgVectorStore built on the shared (default) schema.

    The raw-psycopg2 allowance in `check` cannot see this: the vector store
    qualifies its tables by schema NAME, so a store built with the default
    schema reads and writes the shared `resource_explorer` schema over a
    connection with no search_path. Closed here, at the store's construction
    (which does not connect), so the refusal happens before any connect.
    Any other schema name is a test's own override and is left alone.
    """
    if _truthy(os.environ.get(ALLOW_ENV)):
        return
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        return
    if (schema or "") != shared_vector_schema():
        return
    test = test or os.environ.get("PYTEST_CURRENT_TEST") or "<collection / session setup>"
    raise SharedRegistryAccessError(
        f"REFUSED before connecting: test {test} built a PgVectorStore on the "
        f"SHARED vector-store schema {schema!r} (the {VECTOR_SCHEMA_ENV} default). "
        f"The suite's autouse fixture `isolate_pgvector_schema` sets "
        f"{VECTOR_SCHEMA_ENV} to a per-test scratch schema "
        f"({TEST_SCHEMA_PREFIX}_*), so something overrode it back to the real "
        f"one: an explicit schema={schema!r} argument, or {VECTOR_SCHEMA_ENV} set "
        f"to it. Use the pg_store fixture, or pass a schema named "
        f"{TEST_SCHEMA_PREFIX}_*. To deliberately run against the shared schema "
        f"set {ALLOW_ENV}=1 (nobody does by default; coordinate with live peers "
        f"first)."
    )


# --- installation -----------------------------------------------------------

_state: dict[str, Any] = {
    "installed": False,
    "real_psycopg2_connect": None,
    # Schemas of PgVectorStore instances built under the test prefix, so the
    # per-test scratch schema is dropped only if a test actually used it.
    "vector_schemas_built": set(),
}


def _do_connect_listener(dialect, conn_rec, cargs, cparams):  # noqa: ARG001
    check(_params_from_args(tuple(cargs), cparams), strict=True)


def install() -> None:
    """Idempotent. Registers both hooks for the life of the process."""
    if _state["installed"]:
        return
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    event.listen(Engine, "do_connect", _do_connect_listener)
    try:
        import psycopg2
    except ImportError:
        psycopg2 = None
    if psycopg2 is not None:
        _state["real_psycopg2_connect"] = psycopg2.connect

        def guarded_connect(*args, **kwargs):
            check(_params_from_args(args, kwargs), strict=False)
            return _state["real_psycopg2_connect"](*args, **kwargs)

        guarded_connect.__wrapped__ = _state["real_psycopg2_connect"]  # type: ignore[attr-defined]
        psycopg2.connect = guarded_connect
    from resource_explorer.vector_store_pg import PgVectorStore

    real_init = PgVectorStore.__init__

    def guarded_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)   # builds config only; never connects
        schema = self._config.schema
        check_vector_schema(schema)
        _state["vector_schemas_built"].add(schema)

    guarded_init.__wrapped__ = real_init  # type: ignore[attr-defined]
    PgVectorStore.__init__ = guarded_init  # type: ignore[method-assign]
    _state["installed"] = True


# --- default isolation of the URL settings (used by conftest's autouse fixture)

SHARED_DB_URL_SETTINGS = ("REGISTRY_DATABASE_URL", "METRICS_DATABASE_URL", "FEEDBACK_DATABASE_URL")


def isolate_url_settings(environ: Mapping[str, str], setenv: Callable[[str, str], None],
                         tmp_dir: Any) -> list[str]:
    """Decide and apply the default isolation; return the names it replaced.

    Pure over `environ`, so a test can drive both branches with a fake
    environment instead of depending on the ambient CI flag. CI
    (GITHUB_ACTIONS=true) is left untouched: its service container IS the
    registry. Otherwise a setting that is unset, or already addresses the
    shared registry, is pointed at a throwaway SQLite file.
    """
    from resource_explorer.registry_label import is_shared_registry

    if environ.get("GITHUB_ACTIONS", "").lower() == "true":
        return []
    replaced = []
    for name in SHARED_DB_URL_SETTINGS:
        current = environ.get(name)
        if current is None or is_shared_registry(current):
            setenv(name, f"sqlite:///{tmp_dir}/{name.lower()}.db")
            replaced.append(name)
    return replaced
