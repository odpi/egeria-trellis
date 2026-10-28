"""Step 2a of docs/runtime-architecture-plan.md: the worker role, leader
election, the un-looped web lifespan, and the one shared sync pool.

What each group is actually pinning, and why it would not be caught
otherwise:

* **Leader election** — the property is "two processes, one leader." A
  single-process test cannot observe it, so these take two independent
  locks on two independent connections against the real Postgres and
  assert the second loses. Marked `requires_pgvector`: `pg_try_advisory_
  lock` has no in-memory equivalent, and a mocked one would pin the mock.
* **The web lifespan starts no loop** — the regression this guards is
  silent by construction. If `--no-embed-worker` still started the
  scheduler, everything would keep working; you would only find out when
  two processes double-fired a schedule, which looks like a scheduling
  bug rather than a wiring bug.
* **The shared pool's timeout** — the incident's containment. A test that
  only checked the happy path would pass against a pool with the timeout
  removed.
* **nest_asyncio** — asserted as an import-graph fact, not behaviour,
  because the harm was a global patch applied at import time.
"""
from __future__ import annotations

import ast
import os
import threading
import time
import uuid
from pathlib import Path

import pytest

from resource_explorer import concurrency
from resource_explorer.leader_election import (
    ALL_LOCK_NAMES,
    KEY_NAMESPACE,
    LeaderLock,
    advisory_key,
    documented_keys,
)

PKG = Path(__file__).resolve().parents[1] / "resource_explorer"


def _registry_url() -> str:
    """A Postgres URL for the advisory-lock tests.

    Outside CI the conftest guard points the registry at a throwaway SQLite
    file, where election is a deliberate no-op, so both locks "won" and these
    tests failed on every local run with Postgres reachable. Advisory locks
    write nothing and every test uses a unique name, so the PGVECTOR_*
    instance is safe to lock against, addressed through the per-test scratch
    schema that the shared-registry guard admits."""
    from urllib.parse import quote

    from resource_explorer.config import get_config

    url = get_config().registry.database_url
    if url.startswith("postgresql"):
        return url
    host = os.environ.get("PGVECTOR_HOST")
    if not host:
        pytest.skip("no Postgres configured (PGVECTOR_HOST unset) for the advisory-lock tests")
    user = quote(os.environ.get("PGVECTOR_USER", ""), safe="")
    pw = quote(os.environ.get("PGVECTOR_PASSWORD", ""), safe="")
    # The per-test scratch schema (conftest's isolate_pgvector_schema) is the name the shared-registry guard
    # admits; an advisory lock never touches it, so it need not exist.
    schema = os.environ.get("PGVECTOR_SCHEMA", "resource_explorer_test")
    return (f"postgresql://{user}:{pw}@{host}:{os.environ.get('PGVECTOR_PORT', '5432')}/"
            f"{os.environ.get('PGVECTOR_DBNAME', 'postgres')}?options=-csearch_path%3D{schema}")


# ── B. Leader election ──────────────────────────────────────────────────

class TestAdvisoryKeys:
    def test_keys_are_stable(self):
        """Pinned values, not a recomputation of the same expression.

        A key derived from a namespace is only useful if the namespace
        never moves: across a rolling restart, an old and a new process
        computing different keys would each become leader of their own
        key and both run every loop — the exact failure election exists
        to prevent, presenting as duplicated work rather than as an
        error. Asserting the literals makes a namespace change a failing
        test instead of a silent split.
        """
        assert documented_keys() == {
            "scheduler": -6561321191492868153,
            "bootstrap-monitor": 5582179307941343378,
            "egeria-resync": -3163458856154549756,
            # Not a loop — the one-shot that creates RE's draft GovernanceZone
            # at worker startup (2026-09-04, plan §4). It takes a lock for the
            # same reason: N workers starting together would each race to
            # create the same zone, and pyegeria's generic element create has
            # no upsert.
            "draft-zone-bootstrap": 3533934354696901714,
            # The engine-host claim sweep (engine_host.py), started only with RE_ENGINE_HOST_ENABLED on.
            "engine-host": 54773764437381287,
        }

    def test_keys_are_distinct_and_fit_int64(self):
        keys = list(documented_keys().values())
        assert len(set(keys)) == len(keys)
        for k in keys:
            assert -(2 ** 63) <= k < 2 ** 63

    def test_namespace_is_part_of_the_key(self):
        assert advisory_key("scheduler") != advisory_key("Scheduler")
        assert KEY_NAMESPACE == "resource-explorer/worker"

    def test_every_lock_name_is_documented(self):
        assert set(documented_keys()) == set(ALL_LOCK_NAMES)


class TestNonPostgresIsANoOp:
    def test_sqlite_registry_grants_leadership_with_a_warning(self, caplog):
        """The registry is Postgres-only (plan §3), but a code path that
        still builds a SQLite one must not crash — and must not silently
        look like a won election either."""
        lock = LeaderLock("scheduler", database_url="sqlite:///:memory:")
        with caplog.at_level("WARNING"):
            assert lock.acquire() is True
        assert "no-op" in caplog.text
        lock.release()
        assert lock.held is False


@pytest.mark.requires_pgvector
class TestLeaderElectionAgainstPostgres:
    """Deliberately unique per run: advisory locks are database-wide, not
    schema-scoped, so a fixed name here would contend with the developer's
    own running `resource-explorer web` on the same Postgres."""

    def _name(self) -> str:
        return f"test-{os.getpid()}-{uuid.uuid4().hex[:8]}"

    def test_only_one_of_two_connections_wins(self):
        name = self._name()
        first, second = LeaderLock(name, _registry_url()), LeaderLock(name, _registry_url())
        try:
            assert first.acquire() is True
            assert second.acquire() is False, (
                "a second process took the same advisory key — leader "
                "election is not actually excluding anyone"
            )
            assert first.held and not second.held
        finally:
            first.release()
            second.release()

    def test_release_hands_leadership_over(self):
        name = self._name()
        first, second = LeaderLock(name, _registry_url()), LeaderLock(name, _registry_url())
        try:
            assert first.acquire() is True
            assert second.acquire() is False
            first.release()
            assert second.acquire() is True, (
                "the key stayed held after release — a worker restart would "
                "never regain its own loops"
            )
        finally:
            first.release()
            second.release()

    def test_release_is_idempotent(self):
        lock = LeaderLock(self._name(), _registry_url())
        assert lock.acquire() is True
        lock.release()
        lock.release()
        assert lock.held is False

    def test_different_loops_do_not_contend(self):
        suffix = uuid.uuid4().hex[:8]
        a = LeaderLock(f"test-a-{suffix}", _registry_url())
        b = LeaderLock(f"test-b-{suffix}", _registry_url())
        try:
            assert a.acquire() is True
            assert b.acquire() is True, (
                "two different loops collided on one key — the derivation "
                "is not distinguishing them"
            )
        finally:
            a.release()
            b.release()


# ── A. The worker role ──────────────────────────────────────────────────

class TestRunWorker:
    def test_start_and_stop_within_the_bound(self, monkeypatch):
        """run_worker must return when its stop event is set, promptly,
        even though its supervisors are waiting on 10-15 minute
        intervals. If the supervisors waited on a plain sleep rather than
        the shared event, this hangs — which is what "shuts down within a
        bound" is a rule about."""
        import resource_explorer.worker as worker

        started, stopped = [], []

        def _spec(name, lock_name, interval):
            return worker.LoopSpec(
                name=name, lock_name=lock_name, interval_seconds=interval,
                start=lambda n=name: started.append(n),
                stop=lambda n=name: stopped.append(n),
            )

        monkeypatch.setattr(worker, "loop_specs", lambda: [
            _spec("scheduler", "scheduler", 900),
            _spec("bootstrap-monitor", "bootstrap-monitor", 600),
        ])
        monkeypatch.setattr(worker, "_reconcile_orphaned_runs", lambda: None)
        monkeypatch.setattr(worker, "_warm_survey_definition_cache", lambda: None)
        monkeypatch.setattr(
            worker, "LeaderLock",
            lambda name: type("L", (), {"key": 1, "acquire": lambda s: True,
                                        "release": lambda s: None})(),
        )

        stop = threading.Event()
        done = threading.Event()

        def _go():
            worker.run_worker(embedded=True, stop_event=stop, shutdown_timeout=5)
            done.set()

        t = threading.Thread(target=_go, daemon=True)
        t.start()

        deadline = time.monotonic() + 5
        while sorted(started) != ["bootstrap-monitor", "scheduler"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sorted(started) == ["bootstrap-monitor", "scheduler"]

        stop.set()
        assert done.wait(timeout=10), "run_worker did not return within its bound"
        assert sorted(stopped) == ["bootstrap-monitor", "scheduler"], (
            "a loop was left running after the worker stopped — its advisory "
            "lock is released while its thread still fires"
        )

    def test_a_loop_that_loses_the_election_is_never_started(self, monkeypatch, caplog):
        import resource_explorer.worker as worker

        started = []
        monkeypatch.setattr(worker, "loop_specs", lambda: [worker.LoopSpec(
            name="scheduler", lock_name="scheduler", interval_seconds=0.05,
            start=lambda: started.append("scheduler"), stop=lambda: None)])
        monkeypatch.setattr(worker, "_reconcile_orphaned_runs", lambda: None)
        monkeypatch.setattr(worker, "_warm_survey_definition_cache", lambda: None)
        # A unit test of leader election should not be making network calls.
        # `_ensure_draft_zone` reaches Egeria on startup (the draft zone, and
        # since 2026-09-08 the private zone's security access control too).
        #
        # This is NOT what made the test flaky, though it was the first
        # diagnosis and it was wrong: with this stubbed, `standby` still takes
        # ~1.06s against ~1.13s unstubbed. The flake is the fixed
        # `time.sleep(0.2)` below, which was always too short for the rest of
        # worker startup. Stubbing stays because it is right on its own terms.
        monkeypatch.setattr(worker, "_ensure_draft_zone", lambda: None)
        monkeypatch.setattr(
            worker, "LeaderLock",
            lambda name: type("L", (), {"key": 1, "acquire": lambda s: False,
                                        "release": lambda s: None})(),
        )

        stop = threading.Event()
        done = threading.Event()

        def _go():
            with caplog.at_level("INFO"):
                worker.run_worker(embedded=False, stop_event=stop, shutdown_timeout=5)
            done.set()

        threading.Thread(target=_go, daemon=True).start()

        # POLL for the condition rather than sleeping a fixed interval and
        # asserting once. The old `time.sleep(0.2)` raced worker startup:
        # measured, the standby line lands at ~1.06s, so 0.2s was never enough
        # and the test passed only when scheduling happened to favour it.
        #
        # A generous ceiling with an early exit costs nothing in the passing
        # case and removes the race entirely. `started` is still asserted after
        # the wait, so a process that wrongly STARTED the loop is caught just as
        # it was before — this loosens the timing, not the assertion.
        deadline = time.time() + 10
        while time.time() < deadline and "standby" not in caplog.text:
            time.sleep(0.05)

        stop.set()
        assert done.wait(timeout=10)
        assert started == [], "a standby process started the loop anyway"
        assert "standby" in caplog.text

    def test_startup_log_line_carries_name_interval_and_leadership(self):
        """Structured enough to answer "which process is running the
        scheduler" from a log grep — the question `make ps` cannot answer,
        because both processes look identical from the outside."""
        src = (PKG / "worker.py").read_text()
        line = src.split('"worker loop started:')[1].split('")')[0]
        for token in ("name=%s", "interval=%ss", "leader=true", "advisory_key=%s"):
            assert token in line, f"{token} missing from the startup log line"
        standby = src.split('"worker loop standby:')[1].split('")')[0]
        assert "leader=false" in standby

    def test_the_one_shots_are_not_leader_gated(self):
        """Both are deliberately unelected, for opposite reasons that are
        each easy to 'fix' wrongly: reconciliation judges ownership per
        row and is safe (and more correct) everywhere; the cache warm
        fills a PROCESS-LOCAL dict, so electing one leader would warm one
        process and leave every other cold."""
        src = (PKG / "worker.py").read_text()
        body = src.split("def run_worker(")[1].split("\ndef ")[0]
        one_shots = body.index("_reconcile_orphaned_runs()")
        supervisors = body.index("_supervise")
        assert one_shots < supervisors
        assert "_warm_survey_definition_cache()" in body


# ── C. The web role starts nothing ──────────────────────────────────────

class TestWebLifespan:
    def _lifespan_source(self) -> str:
        src = (PKG / "web" / "app.py").read_text()
        return src.split("async def _lifespan(")[1].split("\n\n\n")[0]

    def test_lifespan_starts_no_loop_directly(self):
        body = self._lifespan_source()
        for gone in ("start_scheduler", "start_bootstrap_monitor",
                     "start_resync_scheduler", "warm_question_guid_cache"):
            assert gone not in body, (
                f"{gone} is still started from the web lifespan — with "
                "--workers N that is N copies of the loop, which is the "
                "whole reason the worker role exists"
            )

    def test_app_module_does_not_import_the_loop_modules(self):
        """Not just 'does not call' — 'does not import'. An import here is
        how the calls crept back in last time, and it also drags Egeria
        clients into every uvicorn worker's startup path."""
        tree = ast.parse((PKG / "web" / "app.py").read_text())
        top_level = {
            n.module for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.col_offset == 0 and n.module
        }
        assert "resource_explorer.scheduler" not in top_level
        assert "resource_explorer.bootstrap" not in top_level
        assert "resource_explorer.egeria_resync" not in top_level

    def test_no_thread_started_when_not_embedded(self, monkeypatch):
        import resource_explorer.web.app as app_mod

        monkeypatch.setenv("EXPLORER_EMBED_WORKER", "false")
        calls = []
        monkeypatch.setattr(
            "resource_explorer.worker.start_embedded_worker",
            lambda: calls.append("started") or (None, threading.Event()),
        )

        import asyncio

        async def _drive():
            gen = app_mod._lifespan(object())
            await gen.__anext__()
            before = threading.active_count()
            try:
                await gen.__anext__()
            except StopAsyncIteration:
                pass
            return before

        asyncio.run(_drive())
        assert calls == [], (
            "the lifespan embedded a worker despite EXPLORER_EMBED_WORKER=false"
        )

    def test_thread_started_when_embedded(self, monkeypatch):
        import resource_explorer.web.app as app_mod

        monkeypatch.setenv("EXPLORER_EMBED_WORKER", "true")
        stop = threading.Event()
        calls = []
        monkeypatch.setattr(
            "resource_explorer.worker.start_embedded_worker",
            lambda: (calls.append("started"), (None, stop))[1],
        )

        import asyncio

        async def _drive():
            gen = app_mod._lifespan(object())
            await gen.__anext__()
            assert calls == ["started"]
            try:
                await gen.__anext__()
            except StopAsyncIteration:
                pass

        asyncio.run(_drive())
        assert stop.is_set(), "shutdown did not signal the embedded worker to stop"

    @pytest.mark.parametrize("value,expected", [
        ("0", False), ("false", False), ("no", False), ("off", False), ("", False),
        ("1", True), ("true", True), ("TRUE", True), ("yes", True),
    ])
    def test_embed_flag_parsing(self, monkeypatch, value, expected):
        import resource_explorer.web.app as app_mod

        monkeypatch.setenv("EXPLORER_EMBED_WORKER", value)
        assert app_mod._embed_worker_enabled() is expected

    def test_embed_defaults_on_when_unset(self, monkeypatch):
        """`make dev` and every existing .env must keep working unchanged."""
        import resource_explorer.web.app as app_mod

        monkeypatch.delenv("EXPLORER_EMBED_WORKER", raising=False)
        assert app_mod._embed_worker_enabled() is True

    def test_package_import_still_guards_ephemeral_prefect(self):
        """Runs in EVERY uvicorn worker process, because it is at package
        import — which is exactly why the guard lives in __init__.py and
        not in the CLI. Thirteen orphaned ephemeral servers is what the
        CLI-only version would have cost."""
        src = (PKG / "__init__.py").read_text()
        assert 'os.environ.setdefault("PREFECT_SERVER_EPHEMERAL_ENABLED", "false")' in src


# ── D. The one shared bounded pool ──────────────────────────────────────

class TestSharedSyncPool:
    def setup_method(self):
        concurrency.shutdown(wait=False)

    def teardown_method(self):
        concurrency.shutdown(wait=False)

    def test_runs_and_returns(self):
        assert concurrency.run_sync(lambda: 6 * 7) == 42

    def test_size_comes_from_config(self):
        concurrency.get_pool()
        assert concurrency.pool_size() == 8
        assert concurrency.size_source() in ("config", "env")

    def test_size_override_from_env(self, monkeypatch):
        monkeypatch.setenv("EXPLORER_SYNC_POOL_SIZE", "3")
        from resource_explorer import config as config_mod

        monkeypatch.setattr(config_mod, "_config", None)
        concurrency.shutdown(wait=False)
        concurrency.get_pool()
        assert concurrency.pool_size() == 3
        monkeypatch.setattr(config_mod, "_config", None)

    def test_timeout_raises_rather_than_blocking(self):
        """The containment the incident needed. Without it the caller —
        and everything queued behind it — waits on Egeria for ever."""
        from concurrent.futures import TimeoutError as FutureTimeoutError

        release = threading.Event()

        def _slow():
            release.wait(30)
            return "late"

        start = time.monotonic()
        with pytest.raises(FutureTimeoutError):
            concurrency.run_sync(_slow, timeout=0.2)
        elapsed = time.monotonic() - start
        release.set()
        assert elapsed < 5, f"run_sync blocked {elapsed:.1f}s past its 0.2s timeout"

    def test_a_timed_out_worker_is_counted_not_joined(self):
        """An abandoned worker holds a slot in a pool that is now SHARED,
        which the old throwaway pools did not. Counting it is the whole
        difference between a diagnosable slow pool and an inexplicable
        one."""
        from concurrent.futures import TimeoutError as FutureTimeoutError

        release = threading.Event()
        before = concurrency.stuck_worker_count()
        with pytest.raises(FutureTimeoutError):
            concurrency.run_sync(lambda: release.wait(30), timeout=0.2)
        assert concurrency.stuck_worker_count() == before + 1
        release.set()
        deadline = time.monotonic() + 5
        while concurrency.stuck_worker_count() != before and time.monotonic() < deadline:
            time.sleep(0.01)
        assert concurrency.stuck_worker_count() == before, (
            "a worker that finished late was still counted as stuck"
        )

    def test_reentrant_call_runs_inline_instead_of_deadlocking(self, monkeypatch):
        """With a bounded shared pool, a pooled task that submits back into
        the pool can wait on a slot only a task behind it could free. At
        pool size 1 that is a guaranteed hang; the inline path is what
        makes it merely slow."""
        monkeypatch.setenv("EXPLORER_SYNC_POOL_SIZE", "1")
        from resource_explorer import config as config_mod

        monkeypatch.setattr(config_mod, "_config", None)
        concurrency.shutdown(wait=False)

        def _outer():
            assert concurrency.in_pool_thread()
            return concurrency.run_sync(lambda: "inner", timeout=1)

        assert concurrency.run_sync(_outer, timeout=5) == "inner"
        monkeypatch.setattr(config_mod, "_config", None)

    def test_exactly_one_pool_per_process(self):
        assert concurrency.get_pool() is concurrency.get_pool()

    def test_workers_are_daemon_threads(self):
        """Not cosmetic: CPython waits for every non-daemon thread at
        interpreter shutdown, with no way to withdraw one afterwards, so
        a non-daemon worker stuck in pyegeria means the process never
        exits. Daemon-ness is fixed at thread start and inherited from
        the creating thread, which is why the pool spawns its workers
        from a daemon thread instead of letting a request thread create
        them lazily."""
        pool = concurrency.get_pool()
        assert len(pool._threads) == concurrency.pool_size()
        assert all(t.daemon for t in pool._threads), (
            "a pool worker is non-daemon — one stuck call now hangs "
            "process exit past every timeout"
        )

    def test_no_module_builds_its_own_bridging_pool(self):
        """The six sites docs/process-model.md §1.3 inventoried. Listed by
        path rather than counted, so a NEW ad hoc pool in a new file is
        caught too."""
        offenders = []
        for path in PKG.rglob("*.py"):
            if path.name == "concurrency.py":
                continue
            # AST, not a substring search: three of these files now
            # DESCRIBE the pool they used to build, in a comment
            # explaining why they no longer do. A text match would read
            # the explanation as the offence.
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and (
                    getattr(node.func, "id", None) == "ThreadPoolExecutor"
                    or getattr(node.func, "attr", None) == "ThreadPoolExecutor"
                ):
                    offenders.append(str(path.relative_to(PKG)))
        assert offenders == [], (
            "these still construct their own ThreadPoolExecutor instead of "
            f"using resource_explorer.concurrency: {offenders}"
        )


# ── D. nest_asyncio is gone ─────────────────────────────────────────────

class TestNestAsyncio:
    def test_survey_definition_reader_does_not_import_it(self):
        """The incident site. Its bridge is now the shared pool plus a
        fresh asyncio.run in the pool thread — the same shape the other
        five sites already used — so nothing here needs loop
        re-entrancy."""
        tree = ast.parse((PKG / "surveyors" / "survey_definition_reader.py").read_text())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        assert not any(n.startswith("nest_asyncio") for n in names)

    def test_no_module_imports_it(self):
        offenders = []
        for path in PKG.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(
                        a.name.startswith("nest_asyncio") for a in node.names):
                    offenders.append(str(path.relative_to(PKG)))
                elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("nest_asyncio"):
                    offenders.append(str(path.relative_to(PKG)))
        assert offenders == [], f"nest_asyncio is still imported by: {offenders}"

    def test_not_declared_as_a_direct_dependency(self):
        """It stays INSTALLED — pyegeria declares it and applies it at its
        own package import — but RE no longer claims it, so nobody reads
        the pyproject entry as evidence RE depends on the global patch."""
        text = (PKG.parent / "pyproject.toml").read_text()
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert not stripped.startswith('"nest-asyncio'), (
                "nest-asyncio is back as a direct dependency"
            )


class TestPoolDoesNotHoldTheProcessOpen:
    """Found live 2026-09-04, by the SIGUSR1 dump this same step added.

    The worker logged "stopped cleanly", released its advisory locks, and
    then sat there. `concurrent.futures.thread._python_exit` joins every
    pool worker at interpreter shutdown regardless of `wait=False`, and
    `threading._shutdown` waits on their locks because they are not
    daemon threads — so one worker stuck in pyegeria's
    `get_guid_for_name -> nest_asyncio -> select()` frame holds the whole
    process open past every timeout. The old per-call pools had exactly
    the same exposure.

    Asserted as a real subprocess exit, not as "detach_workers was
    called": the property is "the process exits", and a unit test of the
    helper would still pass if the call site were removed.
    """

    def test_a_stuck_worker_does_not_block_interpreter_exit(self, tmp_path):
        import subprocess
        import sys
        import textwrap

        script = tmp_path / "hang.py"
        script.write_text(textwrap.dedent("""
            import threading, sys
            from resource_explorer import concurrency
            block = threading.Event()
            concurrency.submit(block.wait)   # never released: the stuck worker
            concurrency.shutdown(wait=False)
            sys.exit(0)
        """))
        proc = subprocess.run(
            [sys.executable, str(script)], capture_output=True, timeout=60,
        )
        assert proc.returncode == 0, proc.stderr.decode()[-2000:]

    def test_both_roles_shut_the_pool_down_explicitly(self):
        """atexit is too late — the interpreter joins pool threads before
        any atexit handler of ours runs — so each role must do it on its
        own way out."""
        worker_src = (PKG / "worker.py").read_text()
        assert "_pool_shutdown(wait=False)" in worker_src
        cli_src = (PKG / "cli" / "main.py").read_text()
        assert "_pool_shutdown(wait=False)" in cli_src


def test_registry_creates_its_schema_on_a_fresh_postgres(monkeypatch):
    """A fresh database has no resource_explorer schema; the registry must create it
    before its tables, or every CREATE TABLE fails under the pinned search_path.

    The schema is named resource_explorer_test_fresh_<pid>: the shared-registry guard only
    admits resource_explorer_test* schemas, and a pid-unique name cannot collide with a
    peer's run or a live server. Skipped (with the reason) only when the role may not
    create schemas in the database, which is a property of the machine, not of the code."""
    import pytest as _pytest
    try:
        import psycopg2 as _pg
    except ImportError:  # pragma: no cover
        import psycopg as _pg
    from resource_explorer.config import get_config
    url = get_config().registry.database_url
    if not url.startswith("postgresql"):
        _pytest.skip("registry is not on Postgres in this environment")
    base = url.split("?")[0]
    schema = f"resource_explorer_test_fresh_{os.getpid()}"
    try:
        raw = _pg.connect(base, connect_timeout=3)
        raw.autocommit = True
    except Exception as exc:  # pragma: no cover - environment dependent
        _pytest.skip(f"postgres unreachable: {exc}")
    with raw.cursor() as cur:
        cur.execute("SELECT has_database_privilege(current_user, current_database(), 'CREATE')")
        can_create = cur.fetchone()[0]
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    raw.close()
    if not can_create:
        _pytest.skip("the database role lacks CREATE on the database, so a fresh schema "
                     "cannot be made here")
    # Point the registry at a search_path whose schema does not exist yet.
    from resource_explorer.registry import ProjectRegistry
    fresh_url = base + f"?options=-csearch_path%3D{schema}"
    try:
        reg = ProjectRegistry(database_url=fresh_url)
        assert reg.list_all() == []
    finally:
        c = _pg.connect(base); c.autocommit = True
        with c.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        c.close()


def test_worker_preloads_pyegeria_before_threads(monkeypatch):
    """The preload runs on the calling thread and never raises, with or without pyegeria."""
    from resource_explorer import worker as w
    assert w._preload_pyegeria() in (True, False)
    calls = []
    monkeypatch.setattr(w, "_preload_pyegeria", lambda: calls.append("preload") or True)
    import inspect
    src = inspect.getsource(w.run_worker)
    assert "_preload_pyegeria()" in src.split("threading.Thread")[0], "preload must precede the first thread start"
