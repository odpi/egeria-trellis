"""Shared pytest configuration — real (non-mocked) integration test tier.

Neither Resource Explorer nor Egeria Advisor had any real test coverage of
their data-store code before this (both fully mock/manual-script-only) —
migration plan Phase 8 fixes that here. `requires_pgvector`-marked tests need
a live reachable Postgres/pgvector instance; they're auto-skipped (not
errored) when one isn't available, so the rest of the suite stays runnable
with no external services, exactly like every other test in this repo.

Integration tests never touch the real `resource_explorer` schema — that
holds real migrated production data (registry + vectors for the 7 real
projects). Everything here runs against a dedicated, throwaway
`resource_explorer_test` schema, dropped and recreated once per test
session.
"""
from __future__ import annotations

import os

import pytest

# Per-process, NOT a single shared name. The fixture below drops this schema
# CASCADE at session start and teardown, so a fixed name meant any two
# overlapping pytest runs destroyed each other's data mid-test — one session's
# startup DROP wiping the other's tables while it was still using them.
#
# That produced a flake with no stable signature: whichever test happened to be
# touching the schema when the DROP landed failed, so it looked like a different
# problem each time (test_alias_and_group_on_conflict_paths, then
# test_ingest_then_query..., then a fixture ERROR, then
# test_metrics_collector_portability) and passed on every re-run in isolation.
# Reproduced deliberately 2026-08-24 by starting a second run 45s into a full
# suite. CI runs one session so it never saw this; it is a local-development
# failure only, which is the kind that gets dismissed as noise.
_TEST_SCHEMA_PREFIX = "resource_explorer_test"
_TEST_SCHEMA = f"{_TEST_SCHEMA_PREFIX}_{os.getpid()}"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError, PermissionError) as exc:
        return isinstance(exc, PermissionError)  # exists, not ours
    return True


def _sweep_orphan_test_schemas(cur) -> list[str]:
    """Drop `<prefix>_<pid>` schemas whose process is gone.

    Per-process names fix the collision but leak on a hard crash, since the
    teardown that would drop them never runs. Swept at session start rather
    than accumulating — and only for dead PIDs, so a concurrent run is never
    the thing being cleaned up. The bare legacy name is included: it predates
    the suffix and cannot belong to a live session.
    """
    cur.execute(
        "SELECT schema_name FROM information_schema.schemata "
        "WHERE schema_name = %s OR schema_name LIKE %s",
        (_TEST_SCHEMA_PREFIX, f"{_TEST_SCHEMA_PREFIX}\_%"),
    )
    dropped = []
    for (name,) in list(cur.fetchall()):
        if name == _TEST_SCHEMA:
            continue
        suffix = name[len(_TEST_SCHEMA_PREFIX) + 1:]
        if name != _TEST_SCHEMA_PREFIX and not suffix.isdigit():
            continue    # not a session schema at all — `..._backup` is someone's
                        # deliberate copy, and destroying data this fixture does
                        # not own is precisely the bug being fixed here
        if suffix.isdigit() and _pid_alive(int(suffix)):
            continue                       # a live concurrent session — leave it
        cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        dropped.append(name)
    return dropped


def _pgvector_reachable() -> bool:
    try:
        import psycopg2
        from resource_explorer.config import get_config

        cfg = get_config().pgvector
        conn = psycopg2.connect(
            host=cfg.host, port=cfg.port, dbname=cfg.dbname,
            user=cfg.db_user, password=cfg.password, connect_timeout=2,
        )
        conn.close()
        return True
    except Exception:
        return False


_PGVECTOR_AVAILABLE = _pgvector_reachable()


# The test suite must never CLAIM rows from the shared registry's run queue.
# `tests/test_process_roles.py` calls `run_worker()` for real, and the worker
# role starts a queue-claim loop — pointed, by default, at the same Postgres
# several sessions and the developer's own running server share. A unit test
# that picked up someone's queued architecture survey and executed it would be
# a genuinely destructive accident, and an intermittent one.
#
# Set here rather than per-test so it cannot be forgotten by the next test that
# starts a worker. The run-queue tests that DO need consumption turn it back on
# for the single call they are asserting about (see test_run_queue.py).
# Enqueueing, reading and reconciling are unaffected — only claiming.
os.environ.setdefault("EXPLORER_RUN_QUEUE_ENABLED", "false")


def _egeria_reachable() -> bool:
    """Same posture as _pgvector_reachable() — a real Egeria platform is an
    external dependency this test suite must run without. Only checks basic
    HTTP reachability (a 401/403 is still "reachable"); the actual
    requires_egeria-marked tests do their own bearer-token auth."""
    try:
        import httpx
        from resource_explorer.config import get_config

        cfg = get_config().egeria
        resp = httpx.get(f"{cfg.platform_url}/servers", timeout=2, verify=False)
        return resp.status_code < 500
    except Exception:
        return False


_EGERIA_AVAILABLE = _egeria_reachable()


@pytest.fixture
def mock_egeria_client_connections(monkeypatch):
    """Make every pyegeria client construction fail FAST, instead of the real
    ~30s `check_connection()` handshake `pyegeria.core._base_server_client.
    BaseServerClient.__init__` does synchronously against `cfg.platform_url`
    on every `ProjectManager`/`CollectionManager`/`MetadataExpert`/etc.
    construction.

    Root-caused 2026-09-27, `re/tests-fail-fast-without-egeria`: a cluster of
    ~29 tests across test_investigation_routes.py, test_investigation_
    reclassification.py, test_curate_blueprints_route.py, test_web.py's Curate
    router tests, test_cli_workflow_commands.py's TestCurateCommand and
    test_dependency_support.py::TestAgainstLiveEgeria each took 30 or 60
    seconds (one or two unmocked client constructions) whenever Egeria was
    unreachable — accumulating to ~18.5 minutes of pure dead time, enough on
    its own to push a CI run over its 30-minute job timeout (Slice 21a's run
    36290350357, investigated and found unrelated to that slice's own diff;
    see docs/Backlog.md).

    These tests already inject a stub `project_manager`/`collection_manager`
    at their own call sites (see `_StubPM`/`_StubCM` in
    test_investigation_routes.py) — but `_apply_investigation_marker` and
    similar helper functions construct their OWN `MetadataExpert` (or
    equivalent) directly, bypassing that injection entirely. The Egeria call
    these tests reach is not what any of them actually test (they assert on
    registry state and error message text) — mocking the client boundary
    rather than the many individual call sites is Slice 12/17's own established
    pattern for "this dependency isn't the point of the test."

    Patches the SHARED base class every pyegeria client inherits from
    (`BaseServerClient.check_connection`), not each client class individually
    — a construction site added later automatically gets the same fast-fail
    rather than silently reintroducing this hang. Raises
    `PyegeriaConnectionException` (pyegeria's own real exception for "could
    not connect"), which every call site in `resource_explorer` already
    catches via a broad `except Exception` — mirroring what a genuinely
    fast-refused connection raises, so the test still exercises the SAME
    error-handling path as it does against a real (reachable or refused)
    Egeria, just without the 30s wait when the platform is not merely refused
    but actually unreachable.

    Fixtures/tests that need a REAL platform check use `requires_egeria`
    (auto-skipped by `pytest_collection_modifyitems` above) instead — this
    fixture is for tests where the point is registry/CLI/route behavior with
    Egeria incidentally in the call graph.
    """
    from pyegeria.core._base_server_client import BaseServerClient
    from pyegeria.core._exceptions import PyegeriaConnectionException

    def _fail_fast(self) -> str:
        raise PyegeriaConnectionException()

    monkeypatch.setattr(BaseServerClient, "check_connection", _fail_fast)

# Load the phase-4 live-write fixtures (`live_egeria_write_target` and
# friends) as a plugin so `tests/` doesn't have to import them per-module.
# `tests/` is a package (has __init__.py), so this resolves the same way any
# other `tests.*` import does. See tests/live_egeria_write_fixtures.py for
# what it provides and its own coordination-note docstring.
pytest_plugins = ["tests.live_egeria_write_fixtures"]


def pytest_addoption(parser):
    parser.addoption(
        "--corpus", action="store_true", default=False,
        help="run tests that assert over the live shared registry's actual "
             "contents (skipped by default — a concurrent survey in another "
             "session can turn them red in files nobody touched)",
    )
    parser.addoption(
        "--live-egeria-writes", action="store_true", default=False,
        help="run tests marked live_egeria_writes, which perform real "
             "catalogue/delete writes against the shared dev Egeria platform "
             "(skipped by default even when Egeria is reachable — this is a "
             "bigger commitment than requires_egeria's read-only reachability "
             "check, and needs live-peer coordination before every run; see "
             "tests/live_egeria_write_fixtures.py)",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_pgvector: needs a live reachable Postgres/pgvector instance "
        "(auto-skipped when one isn't available)",
    )
    config.addinivalue_line(
        "markers",
        "requires_egeria: needs a live reachable Egeria platform "
        "(auto-skipped when one isn't available). Read-only-safe: this only "
        "certifies reachability, never that the test writes anything. A test "
        "that writes to dev Egeria — even throwaway writes — must additionally "
        "carry live_egeria_writes below; requires_egeria alone is not enough "
        "of a gate for that.",
    )
    config.addinivalue_line(
        "markers",
        "corpus: asserts over whatever the LIVE shared registry happens to "
        "contain (skipped by default; --corpus to run)",
    )
    config.addinivalue_line(
        "markers",
        "live_egeria_writes: performs REAL writes (catalogue, then delete) "
        "against the shared dev Egeria platform. Skipped by default even when "
        "Egeria is reachable — pass --live-egeria-writes to opt in, and get "
        "live-peer coordination first (see tests/live_egeria_write_fixtures.py "
        "and the coordinate-shared-writes skill). Deliberately separate from, "
        "and stricter than, requires_egeria — see that marker's own docstring.",
    )
    config.addinivalue_line(
        "markers",
        "live_egeria: pre-existing, narrower marker used only by "
        "test_dependency_support.py::TestAgainstLiveEgeria — read-only "
        "(queries Egeria technology types), and does its own manual "
        "pytest.skip() rather than being wired into the auto-skip mechanism "
        "below. Registered here only to silence the unknown-marker warning; "
        "left as-is by the phase-4 harness work as a pre-existing, out-of-scope "
        "inconsistency (see PHASE-4-LIVE-HARNESS-IMPLEMENTED.md) rather than "
        "folded into requires_egeria or live_egeria_writes without a separate "
        "decision to do so.",
    )


def _live_egeria_writes_should_skip(egeria_available: bool, flag_enabled: bool) -> bool:
    """Pure gate logic for the `live_egeria_writes` marker — pulled out of
    `pytest_collection_modifyitems` so it can be unit-tested directly (with
    both inputs mocked) without needing a real Egeria or a real pytest run.

    Skip whenever EITHER input says no: unreachable Egeria must never be
    papered over by the flag (that would turn --live-egeria-writes into "try
    to write and get a connection error" instead of a clean skip), and a
    reachable Egeria must never be enough **by itself** (that is exactly the
    gap requires_egeria leaves open — see its docstring in pytest_configure).
    """
    return not (egeria_available and flag_enabled)


#: Set in CI. Auto-skipping is right on a developer laptop with no services
#: running — but it means a CI job whose Postgres service failed to start, or
#: whose credentials are wrong, reports a green suite having silently run none
#: of the integration tier. That is the same blind spot the tier exists to
#: close, one level up: the tests that check production behaviour quietly not
#: running is indistinguishable from them passing.
#:
#: With RE_REQUIRE_POSTGRES=1 an unreachable instance is a hard error instead.
_REQUIRE_POSTGRES = os.environ.get("RE_REQUIRE_POSTGRES", "").strip() not in ("", "0", "false")


def pytest_collection_modifyitems(config, items):
    if _REQUIRE_POSTGRES and not _PGVECTOR_AVAILABLE:
        marked = sum(1 for i in items if "requires_pgvector" in i.keywords)
        pytest.exit(
            f"RE_REQUIRE_POSTGRES is set but Postgres is not reachable at the "
            f"configured host:port, so {marked} requires_pgvector test(s) would "
            f"have been silently skipped. Check the service container and the "
            f"PGVECTOR_* environment variables.",
            returncode=1,
        )

    #: Corpus tests assert over whatever the live shared registry HOLDS, not
    #: over fixtures. That is their value — `security_features` rendering as a
    #: bare empty card on 58 of 60 real repos is a fact no fixture would have
    #: produced — and it is also why they cannot run by default now that five
    #: sessions share one Postgres.
    #:
    #: Measured 2026-08-30: a peer session surveying `egeria_workspaces_git`
    #: caught it mid-run, in a state the security-features reader classifies
    #: into none of its three causes, and turned this suite red in a file that
    #: session had never touched. The worktree split partitions FILES; the
    #: registry is still shared, so data contention is untouched by it.
    #:
    #: Skipped rather than deleted, and opt-in rather than removed: run them
    #: with `--corpus` when the corpus is quiet, which is exactly when their
    #: answer means anything.
    skip_corpus = pytest.mark.skip(
        reason="asserts over the live shared registry — run with --corpus when "
               "no other session is surveying"
    )
    run_corpus = config.getoption("--corpus")

    skip_pgvector = pytest.mark.skip(reason="pgvector/Postgres not reachable at the configured host:port")
    skip_egeria = pytest.mark.skip(reason="Egeria platform not reachable at the configured platform_url")

    run_live_egeria_writes = config.getoption("--live-egeria-writes")
    skip_live_writes = pytest.mark.skip(
        reason="performs real writes against shared dev Egeria — needs both a "
               "reachable platform AND --live-egeria-writes (plus live-peer "
               "coordination before you pass that flag); requires_egeria's "
               "reachability check alone is not enough of a gate for a "
               "write-capable test"
    )
    should_skip_live_writes = _live_egeria_writes_should_skip(_EGERIA_AVAILABLE, run_live_egeria_writes)

    for item in items:
        if not _PGVECTOR_AVAILABLE and "requires_pgvector" in item.keywords:
            item.add_marker(skip_pgvector)
        if not _EGERIA_AVAILABLE and "requires_egeria" in item.keywords:
            item.add_marker(skip_egeria)
        if not run_corpus and "corpus" in item.keywords:
            item.add_marker(skip_corpus)
        if should_skip_live_writes and "live_egeria_writes" in item.keywords:
            item.add_marker(skip_live_writes)


@pytest.fixture(scope="session")
def pg_test_schema():
    """The throwaway schema name integration tests write to — created fresh
    (drop-then-create) once per test session, dropped again at teardown."""
    if not _PGVECTOR_AVAILABLE:
        pytest.skip("pgvector/Postgres not reachable")

    import psycopg2
    from resource_explorer.config import get_config

    cfg = get_config().pgvector
    conn = psycopg2.connect(
        host=cfg.host, port=cfg.port, dbname=cfg.dbname,
        user=cfg.db_user, password=cfg.password,
    )
    conn.autocommit = True
    with conn.cursor() as cur:
        orphans = _sweep_orphan_test_schemas(cur)
        if orphans:
            print(f"\n[conftest] swept {len(orphans)} orphaned test schema(s): "
                  f"{', '.join(orphans)}")
        cur.execute(f'DROP SCHEMA IF EXISTS "{_TEST_SCHEMA}" CASCADE')
        cur.execute(f'CREATE SCHEMA "{_TEST_SCHEMA}"')
    conn.close()

    yield _TEST_SCHEMA

    conn = psycopg2.connect(
        host=cfg.host, port=cfg.port, dbname=cfg.dbname,
        user=cfg.db_user, password=cfg.password,
    )
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{_TEST_SCHEMA}" CASCADE')
    conn.close()


@pytest.fixture
def pg_store(pg_test_schema):
    """A PgVectorStore pointed at the throwaway test schema — never the real
    resource_explorer schema."""
    from resource_explorer.vector_store_pg import PgVectorStore

    store = PgVectorStore(schema=pg_test_schema)
    store.connect()
    yield store
    store.disconnect()


@pytest.fixture
def pg_registry(pg_test_schema):
    """A ProjectRegistry backed by the throwaway test schema — uses the
    database_url override (added for this fixture; see registry.py) rather
    than the real config default, so it never touches production data."""
    from resource_explorer.config import get_config
    from resource_explorer.registry import ProjectRegistry

    cfg = get_config().pgvector
    url = (
        f"postgresql://{cfg.db_user}:{cfg.password}@{cfg.host}:{cfg.port}/{cfg.dbname}"
        f"?options=-csearch_path%3D{pg_test_schema}"
    )
    return ProjectRegistry(database_url=url)


# RE requires login as of 2026-09-04 (docs/runtime-architecture-plan.md §4),
# and `LoginRequiredMiddleware` 401s every `/api/` route without a token. The
# ~300 route tests in this suite predate that and are about route behaviour,
# not about authentication — so the gate is off for the suite by default and
# the tests that ARE about it turn it back on for exactly the app object they
# assert against (`test_login_and_identity.py`, which reloads
# `resource_explorer.web.app` under the policy it wants).
#
# The same shape as `EXPLORER_RUN_QUEUE_ENABLED` above, and for the same
# reason: a cross-cutting behaviour that would otherwise have to be defeated,
# identically, in every one of three hundred fixtures. Set at import rather
# than in a fixture because the policy is resolved once, when the middleware is
# constructed at `web/app.py` import — which happens the first time any test
# module imports the app, before any fixture has run.
os.environ.setdefault("EXPLORER_REQUIRE_LOGIN", "false")

# A JWT secret for the whole session, so a test that mints a token and a test
# that verifies one agree. Without it `auth.jwt_secret()` derives one from the
# hostname and logs a warning per process — correct behaviour, noisy here.
os.environ.setdefault("RE_JWT_SECRET", "resource-explorer-test-secret")


@pytest.fixture(autouse=True)
def ephemeral_prefect(monkeypatch):
    """Force Prefect to run flows in-process, ignoring any configured server.

    Prefect's client honours PREFECT_API_URL, and it loads that from `.env`
    itself — so clearing os.environ is not enough, and a checkout whose .env
    points at a Prefect server that is not running fails every Prefect test
    with "Failed to reach API at http://localhost:4200/api/", which says
    nothing about the behaviour under test.

    Found exactly that way on 2026-08-27: the same tests passed in one checkout
    and failed in another, on ambient environment alone. Both the flow tests
    and the older integration tests depended on it.

    This is `autouse` deliberately. It was opt-in until 2026-08-31, via
    `pytestmark = pytest.mark.usefixtures("ephemeral_prefect")` per module — and
    four Prefect-touching modules never opted in (`test_prefect_dispatch`,
    `test_prefect_status_routes`, `test_survey_card_parity`,
    `test_no_silent_success`). A guard that only works when every future module
    remembers to ask for it is the same shape as the orphaned-slug ratchet: it
    reports safety it is not providing. Autouse removes the opt-in step rather
    than asking people to remember it.

    The surviving `pytestmark` lines are redundant now, not wrong; they are left
    so that `git log -S` on them still leads here.

    **The `prefect` import below is load-bearing in a way its position hides.**
    It sits in the fixture body, and autouse means every test in the suite now
    imports Prefect — including the ~2,900 with nothing to do with it. That is
    free today, since `prefect` is a declared dependency and always installed.
    It stops being free in a slimmer environment: a subset run without Prefect
    installed would fail in *every* test rather than only the Prefect ones, and
    the traceback would not obviously point at an autouse fixture. If that ever
    happens, this is why. Raised in review of the autouse change.

    Note what this does NOT fix: an intermittent failure of
    `test_prefect_integration.py::...::test_local_flow_execution_fallback`
    observed once on 2026-08-31 and not reproduced on rerun. The leading
    hypothesis — that a non-opted-in module poisons Prefect's cached client
    first — was tested and falsified: it predicts a deterministic failure under
    `-p no:randomly`, and two fixed-order runs disagreed. See Backlog.
    """
    from prefect.settings import (
        PREFECT_API_URL,
        PREFECT_SERVER_EPHEMERAL_ENABLED,
        temporary_settings,
    )

    for var in ("PREFECT_API_URL", "PREFECT_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    # resource_explorer/__init__.py forces PREFECT_SERVER_EPHEMERAL_ENABLED=false
    # process-wide (2026-09-04 fix for a real leak — see its docstring): 13
    # orphaned `prefect.server.api.server:create_app` subprocess servers were
    # found on this machine, spawned exactly the way this fixture's own docstring
    # describes ("Prefect's client... loads that from `.env`") whenever no real
    # API was reachable. That guard is correct for the *app*, but this fixture's
    # actual job — proving Prefect's own flow-engine orchestration (topological
    # order, guards, joins; see test_prefect_survey_flow.py) — genuinely needs
    # a Prefect API to talk to, and ephemeral is the only sane one in a test.
    #
    # This must be an explicit `temporary_settings` update, NOT an env var
    # (`monkeypatch.setenv` alone does not work here — found live 2026-09-04
    # debugging why this exact fixture still failed after adding it).
    # `prefect.context` bakes a frozen `GLOBAL_SETTINGS_CONTEXT` at its own
    # *first import*, reading the environment as it stood then — which, in
    # this suite, is already after `resource_explorer/__init__.py`'s
    # `os.environ.setdefault("PREFECT_SERVER_EPHEMERAL_ENABLED", "false")` ran
    # (package import happens at collection, before any test body). Every
    # later `temporary_settings(...)` call derives its settings via
    # `context.settings.copy_with_update(updates=...)` — copying from that
    # already-frozen base and applying only the keys named in `updates`, never
    # re-reading `os.environ` for the rest — so an env var set after Prefect's
    # first import cannot reach this field at all; only passing it in
    # `updates` (as done below) can.
    with temporary_settings({
        PREFECT_API_URL: None,
        PREFECT_SERVER_EPHEMERAL_ENABLED: True,
    }):
        yield
