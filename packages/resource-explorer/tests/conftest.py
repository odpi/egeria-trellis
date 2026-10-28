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

# Fail-closed guard against tests reaching the SHARED registry (incident
# 2026-10-02; see tests/shared_registry_guard.py and
# docs/design-notes/implemented/TEST-SHARED-REGISTRY-GUARD-IMPLEMENTED.md).
# Installed at import, before any probe below opens a connection, at the
# SQLAlchemy/psycopg2 connect choke point so a cached config cannot defeat it.
# Opt-in for a deliberate run: RE_TESTS_ALLOW_SHARED_REGISTRY=1.
from tests import live_egeria_tier, shared_registry_guard, tracked_tree_guard

shared_registry_guard.install()

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
        suffix = suffix.split("_", 1)[0]   # `<pid>_v<n>` per-test scratch schemas
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


#: Probed lazily and only when a live tier was opted into (see
#: tests/live_egeria_tier.py): a default run must not contact the platform.
_egeria_probe_cache: list[bool] = []


def _egeria_available() -> bool:
    if not _egeria_probe_cache:
        _egeria_probe_cache.append(_egeria_reachable())
    return _egeria_probe_cache[0]


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
        "--live-egeria-reads", action="store_true", default=False,
        help="run the live READ tier (requires_egeria / live_egeria) against "
             "the shared dev Egeria platform (or set RE_LIVE_EGERIA_READS=1). "
             "Skipped by default even when Egeria is reachable; run it after "
             "a peer round. Never runs in CI.",
    )
    parser.addoption(
        "--live-egeria-writes", action="store_true", default=False,
        help="ALSO needs RE_LIVE_EGERIA_WRITES_CLEARED=<who>/<UTC time> "
             "naming the peer round, else the tier is skipped. Implies reads. "
             "run tests marked live_egeria_writes, which perform real "
             "catalogue/delete writes against the shared dev Egeria platform "
             "(skipped by default even when Egeria is reachable — this is a "
             "bigger commitment than requires_egeria's read-only reachability "
             "check, and needs live-peer coordination before every run; see "
             "tests/live_egeria_write_fixtures.py)",
    )


def pytest_sessionstart(session):
    """Fingerprint the tracked tree so pytest_sessionfinish can prove the run left it alone."""
    if hasattr(session.config, "workerinput"):  # xdist worker: the controller checks
        return
    session.config._tracked_tree_before = tracked_tree_guard.snapshot()


def pytest_sessionfinish(session, exitstatus):
    if hasattr(session.config, "workerinput"):
        return
    before = getattr(session.config, "_tracked_tree_before", None)
    touched = tracked_tree_guard.changed(before, tracked_tree_guard.snapshot())
    if touched:
        print("\n\nTEST RUN MODIFIED THE WORKING TREE under packages/resource-explorer "
              "(tests must write to tmp_path, never to tracked files):\n  "
              + "\n  ".join(touched))
        session.exitstatus = 1


def pytest_report_header(config):
    """One line naming the live Egeria tier when it is ON: URL and user id only."""
    decision = getattr(config, "_live_egeria_decision", None)
    if decision is None:   # header prints before collection: decide now, same inputs
        decision = live_egeria_tier.decide(
            os.environ,
            reads_flag=config.getoption("--live-egeria-reads"),
            writes_flag=config.getoption("--live-egeria-writes"),
            reachable=_egeria_available,
        )
        config._live_egeria_decision = decision
    if not decision.reads_on:
        return None
    from resource_explorer.config import get_config

    cfg = get_config().egeria
    return live_egeria_tier.banner(decision, cfg.platform_url, cfg.user_id)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_pgvector: needs a live reachable Postgres/pgvector instance "
        "(auto-skipped when one isn't available)",
    )
    config.addinivalue_line(
        "markers",
        "requires_egeria: the live READ tier. OPT-IN since 2026-10-05: skipped "
        "by default even when Egeria is reachable; run with --live-egeria-reads "
        "(or RE_LIVE_EGERIA_READS=1) after a peer round. Never runs in CI. "
        "Also skipped when the platform is unreachable. Read-only-safe: this "
        "only certifies reachability, never that the test writes anything. A test "
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
        "Egeria is reachable — needs --live-egeria-writes AND "
        "RE_LIVE_EGERIA_WRITES_CLEARED=<who>/<UTC time> (non-empty; the flag "
        "alone skips the tier) naming the peer round: another session's done "
        "is a statement about itself, never a clearance from the others; the "
        "peer round is a question to every live peer, and the answer set is "
        "what goes in the variable. Get live-peer coordination first (see tests/live_egeria_write_fixtures.py "
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
    decision = live_egeria_tier.decide(
        os.environ,
        reads_flag=config.getoption("--live-egeria-reads"),
        writes_flag=config.getoption("--live-egeria-writes"),
        reachable=_egeria_available,
    )
    config._live_egeria_decision = decision
    skip_egeria = pytest.mark.skip(reason=decision.read_skip_reason or "")
    skip_live_writes = pytest.mark.skip(reason=decision.write_skip_reason or "")
    should_skip_live_writes = _live_egeria_writes_should_skip(decision.reads_on, decision.writes_on)

    for item in items:
        if not _PGVECTOR_AVAILABLE and "requires_pgvector" in item.keywords:
            item.add_marker(skip_pgvector)
        if not decision.reads_on and (
                "requires_egeria" in item.keywords or "live_egeria" in item.keywords):
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

# Import the app ONCE, here, before any test has patched anything. Route modules
# bind `get_current_user` by name (`from ..auth import get_current_user`) at their
# FIRST import, and the first import used to happen inside whichever test fixture
# got there first — several of which monkeypatch `resource_explorer.auth.get_current_user`
# just before `from resource_explorer.web.app import app`. The route modules then kept
# that fixture's lambda for the rest of the process (tests/test_curate.py's
# "peterprofile" answered a later test that expected the signed-in "dan").
# Importing here makes every route module bind the real function; a test that
# wants a different caller patches it with monkeypatch, which is undone at teardown.
import resource_explorer.web.app  # noqa: E402,F401


@pytest.fixture(autouse=True)
def issue_117_block_off_for_legacy_delete_path_tests(request, monkeypatch):
    """The ISSUE-117 hard block (RE_ISSUE_117_BLOCK_OFF) stops every archive and delete RE could
    send to Egeria. The older catalogue tests exercise that frozen path against the fake and keep doing so
    with the switch off; ONLY tests/test_catalogue_issue117_block.py runs with it on (the shipped state)."""
    if request.module.__name__.endswith("test_catalogue_issue117_block"):
        return
    monkeypatch.setenv("RE_ISSUE_117_BLOCK_OFF", "tests/legacy-delete-path-fixture")


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


@pytest.fixture(autouse=True)
def clear_survey_definition_reader_caches():
    """Clear survey_definition_reader's module-level caches before every test.

    `survey_definition_reader.py` keeps four process-lifetime caches
    (`_question_guid_cache`, `_candidates_cache`, `_fetch_cache`,
    `_process_guid_cache`) behind a single `clear_caches()` testing hook. That
    hook already existed and was already called by hand at the top of
    `test_survey_definition_reader.py`'s own tests — but three tests in
    `test_survey_definitions_routes.py::TestListCandidates`
    (`test_returns_step_detail_for_valid_candidate`,
    `test_egeria_step_enriched_with_produced_annotation_types`,
    `test_egeria_native_processes_excludes_delete_kind`) never called it, and
    `patch()` on the reader's own method cannot see a hit that was already
    served from `_candidates_cache` — a real call earlier in the suite
    populates it keyed by `(full_scan, technology_type, survey_kind)`, and
    whichever of these three tests runs after that call in the same process
    gets the stale cached candidates instead of exercising its own patched
    behavior. Order-dependent, passes every one of the three in isolation.

    Autouse removes the opt-in step (the same reasoning `ephemeral_prefect`
    above gives for its own autouse switch) rather than trusting every test
    module that touches this reader to remember to call `clear_caches()`
    itself.

    **A second, separate process-lifetime singleton, found 2026-09-28 while
    investigating why the SAME three tests were still red in CI after this
    fixture landed.** `web/routes/survey_definitions.py` keeps its own
    module-level `_tech_type_catalog` (a lazily-constructed
    `EgeriaTechTypeCatalog`, built once and reused — see that module's own
    comment on why: "callers should share one instance rather than refetch
    per request"). Right for production, wrong for a test process: once any
    test's route call constructs it for real, every LATER test — including
    one that patches `EgeriaTechTypeCatalog.get_produced_annotation_types`
    on the class — still calls through the SAME pre-existing instance, whose
    own internal `_all_types_cache`/`_detail_cache` (instance attributes,
    not module globals, so `clear_caches()` above never touched them) may
    already hold a real-or-empty answer from before the patch was ever
    applied. Reset here too, by the same reasoning as the reader's caches:
    a process-lifetime singleton is a production optimisation, not a test
    contract, and every test should see a fresh one.
    """
    from resource_explorer.surveyors.survey_definition_reader import clear_caches

    clear_caches()
    yield
    clear_caches()


@pytest.fixture(autouse=True)
def clear_tech_type_catalog_singleton():
    """Reset `web.routes.survey_definitions`'s module-level `EgeriaTechTypeCatalog`
    singleton before and after every test — see
    `clear_survey_definition_reader_caches`'s docstring above for why this is
    a second instance of the exact same problem in a different module.
    """
    import resource_explorer.web.routes.survey_definitions as survey_definitions_route

    survey_definitions_route._tech_type_catalog = None
    yield
    survey_definitions_route._tech_type_catalog = None


@pytest.fixture(autouse=True)
def isolate_database_url_settings(tmp_path, monkeypatch):
    """Point every setting that defaults to the SHARED Postgres at a throwaway
    SQLite file, so a test that forgets to override one cannot leak into it.

    Found 2026-10-05: test_ingest_then_query_returns_real_retrieved_content
    patched the registry and vector store but not MetricsCollector, whose
    METRICS_DATABASE_URL has its own shared default; every local run with
    pgvector reachable wrote 'fixtureproj' rows into the shared query_log.

    A value that is unset, or that already addresses the shared registry (a
    developer's exported URL), is replaced. A deliberate override (a temp
    SQLite path, a resource_explorer_test* schema) is left alone, and so is
    CI, whose service container deliberately IS the registry. A test overrides
    afterwards with its own monkeypatch.setenv.
    """
    import resource_explorer.config as _config_module

    shared_registry_guard.isolate_url_settings(os.environ, monkeypatch.setenv, tmp_path)
    _config_module._config = None
    yield


@pytest.fixture(autouse=True)
def reset_cached_config():
    """Drop `resource_explorer.config._config` before and after every test.

    `get_config()` caches the first `ExplorerConfig` for the life of the
    process, so a test that sets `REGISTRY_DATABASE_URL` (or any env var) in a
    fixture was silently ignored once anything had called it earlier, and the
    test fell through to the SHARED registry (incident 2026-10-02). Resetting
    makes a per-test `monkeypatch.setenv` effective. The connect-time guard in
    tests/shared_registry_guard.py is the backstop for URLs that arrive by any
    other route.
    """
    import resource_explorer.config as _config_module

    _config_module._config = None
    yield
    _config_module._config = None


_scratch_counter = {"n": 0}


@pytest.fixture(autouse=True)
def isolate_pgvector_schema(monkeypatch, isolate_database_url_settings, reset_cached_config):
    """Point PGVECTOR_SCHEMA at a per-test scratch schema, never the shared one.

    The guard's raw-psycopg2 allowance cannot see a default `PgVectorStore()`:
    it qualifies tables by schema NAME, so it would read and write the shared
    `resource_explorer` schema. This sets PGVECTOR_SCHEMA to
    `resource_explorer_test_<pid>_v<n>` (a test's own override still wins), and
    shared_registry_guard refuses a store built on the shared name.

    LAZY: setting the env var connects to nothing. The schema is created by the
    store itself (CREATE SCHEMA IF NOT EXISTS on connect) and dropped here at
    teardown only if a store was actually built on it AND pgvector is
    reachable, so a run without pgvector never attempts it.
    """
    _scratch_counter["n"] += 1
    scratch = f"{_TEST_SCHEMA_PREFIX}_{os.getpid()}_v{_scratch_counter['n']}"
    monkeypatch.setenv("PGVECTOR_SCHEMA", scratch)
    yield scratch
    if scratch in shared_registry_guard._state["vector_schemas_built"]:
        shared_registry_guard._state["vector_schemas_built"].discard(scratch)
        if _PGVECTOR_AVAILABLE:
            import psycopg2
            from resource_explorer.config import PgVectorConfig

            cfg = PgVectorConfig()
            conn = psycopg2.connect(
                host=cfg.host, port=cfg.port, dbname=cfg.dbname,
                user=cfg.db_user, password=cfg.password,
            )
            conn.autocommit = True
            try:
                with conn.cursor() as cur:
                    cur.execute(f'DROP SCHEMA IF EXISTS "{scratch}" CASCADE')
            finally:
                conn.close()


# ── Brief I: who an Egeria call acts as, for tests that fake the clients ─────
# Nothing falls back to the service account any more: a code path that builds an Egeria client
# with no signed-in caller and no declared daemon job raises NoCallerIdentity. Tests whose point
# is NOT identity opt in to one of these, explicitly, per module or per test.

@pytest.fixture
def signed_in_caller():
    """A signed-in person with an Egeria token, as the web middleware would publish them."""
    from resource_explorer.a2a_auth import CallerIdentity, current_caller

    reset = current_caller.set(CallerIdentity(user_id="test-caller", egeria_token="tok-test-caller",
                                              auth_source="app-jwt"))
    try:
        yield "test-caller"
    finally:
        current_caller.reset(reset)


@pytest.fixture
def as_daemon():
    """A declared daemon job (the scheduler's), as a worker loop would declare it."""
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

    with acting_as(Daemon(DaemonReason.SCHEDULER)) as identity:
        yield identity


# ── Brief I round 2: no test reaches a real Egeria platform ─────────────────
_LIVE_MARKERS = ("requires_egeria", "live_egeria", "live_egeria_writes")


class RealEgeriaBlocked(RuntimeError):
    """A test tried to send a request to a real Egeria platform without a live marker."""


def _is_pyegeria_client(cls) -> bool:
    """A real pyegeria client class: a subclass of pyegeria's client bases (or the Egeria* facades,
    which compose clients rather than inherit). By class, not by `__module__` text."""
    import inspect

    if not inspect.isclass(cls):
        return False
    from pyegeria.core._base_platform_client import BasePlatformClient
    from pyegeria.core._base_server_client import BaseServerClient

    if issubclass(cls, (BasePlatformClient, BaseServerClient)):
        return True
    # The REAL facades, from their own modules (a test may have patched `pyegeria.EgeriaTech`).
    from pyegeria.egeria_cat_client import EgeriaCat
    from pyegeria.egeria_config_client import EgeriaConfig
    from pyegeria.egeria_tech_client import EgeriaTech

    return issubclass(cls, (EgeriaTech, EgeriaCat, EgeriaConfig))


@pytest.fixture(autouse=True)
def no_real_egeria(request, monkeypatch):
    """Every pyegeria client comes from `egeria_clients` (the ban test enforces it), so this
    guards there: the factory refuses to construct a REAL pyegeria class. Fakes (any class not
    from pyegeria) pass untouched.
    A test marked requires_egeria / live_egeria / live_egeria_writes is left alone."""
    if any(request.node.get_closest_marker(m) for m in _LIVE_MARKERS):
        yield
        return
    from resource_explorer import egeria_clients

    def guarded_build(cls, *args):
        # A real pyegeria class is refused BEFORE it is constructed: some pyegeria constructors
        # already call the platform (`/api/about`). Fakes and mocks pass through.
        if _is_pyegeria_client(cls):
            raise RealEgeriaBlocked(
                f"a test tried to build a real {cls.__name__} (it would reach a real Egeria "
                "platform); mark it requires_egeria / live_egeria_writes, or fake the client")
        return cls(*args)

    monkeypatch.setattr(egeria_clients, "_build", guarded_build)
    yield


#: Platforms existing test fixtures hard-code. Allowed in tests only, so a fixture that names
#: the old default `https://localhost:9443` keeps working whatever EGERIA_PLATFORM_URL the run
#: sets; the guard above still refuses to build any REAL client, so nothing is sent anywhere.
_FIXTURE_PLATFORMS = ("https://localhost:9443", "http://localhost:9443",
                      "https://host.docker.internal:9443")


@pytest.fixture(autouse=True)
def allow_fixture_platforms(request, monkeypatch):
    if any(request.node.get_closest_marker(m) for m in _LIVE_MARKERS):
        return
    from resource_explorer import egeria_clients

    real = egeria_clients.allowed_platforms
    monkeypatch.setattr(egeria_clients, "allowed_platforms",
                        lambda: real() | {egeria_clients.origin_of(u) for u in _FIXTURE_PLATFORMS})


@pytest.fixture
def allow_example_platform(monkeypatch):
    """For tests whose fake entities name `https://egeria.example`: allow that origin, as a
    deployment would with EGERIA_ALLOWED_PLATFORM_URLS (Brief I round 2)."""
    from resource_explorer import egeria_clients

    real = egeria_clients.allowed_platforms
    monkeypatch.setattr(egeria_clients, "allowed_platforms",
                        lambda: real() | {egeria_clients.origin_of("https://egeria.example")})


@pytest.fixture(autouse=True)
def fresh_zone_controls_report(monkeypatch):
    """The start-up zone check's answer (`egeria_identity._zone_controls_state`) is per-process; each test starts
    with it unasked, so one test's "ok" or "blocked" never decides another's publish."""
    from resource_explorer import egeria_identity

    monkeypatch.setattr(egeria_identity, "_zone_controls_state", None)
