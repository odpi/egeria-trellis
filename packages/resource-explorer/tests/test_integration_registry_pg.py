"""The registry, against real Postgres — the divergences the FK pragma can't close.

Why this tier exists. Production runs the registry on Postgres; every other
registry test runs it on SQLite. That made SQLite a strictly *weaker* mirror of
production: a write SQLite accepts and Postgres rejects passes the whole suite
and fails live. Three incidents on record, all the same shape —

  * `remove()` never deleted five child tables; invisible on SQLite (no FK
    enforcement), a hard FK crash on Postgres (see registry.remove()'s comment)
  * `remove_database()` had the same deletion-order bug (its own comment)
  * 2026-08-23: three orphaned project_analysis_findings rows in the live
    Postgres for an unregistered slug, absorbed by a caller's broad
    except+log.warning so nothing surfaced

`PRAGMA foreign_keys=ON` (added 2026-08-23) closes the FK half on SQLite. It
cannot close the rest: Postgres has static column types where SQLite has
dynamic affinity, and stricter GROUP BY rules. Those need real Postgres, which
is what this file is.

Auto-skipped when Postgres isn't reachable, same posture as every other
integration test here — the suite still runs with no external services.
"""
from __future__ import annotations

import json

import pytest

from resource_explorer.registry import Project, ProjectRegistry

pytestmark = pytest.mark.requires_pgvector


@pytest.fixture
def pg_registry(pg_test_schema):
    """A registry pointed at the throwaway integration schema, never the real
    `resource_explorer` one (which holds live production data)."""
    from resource_explorer.config import get_config

    cfg = get_config().pgvector
    url = (f"postgresql://{cfg.db_user}:{cfg.password}@{cfg.host}:{cfg.port}"
           f"/{cfg.dbname}?options=-csearch_path%3D{pg_test_schema}")
    return ProjectRegistry(database_url=url)


@pytest.fixture
def pg_project(pg_registry):
    slug = "pg_itest_proj"
    if pg_registry.get(slug) is None:
        pg_registry.add(Project(slug=slug, display_name="PG Integration Test",
                                github_url="https://github.com/test/pg-itest",
                                collections=[]))
    yield slug
    pg_registry.remove(slug)


class TestForeignKeysAreReallyEnforced:
    """The SQLite pragma should now make these behave identically in both
    backends. That is the claim; this is where it is checked against the
    backend that was always strict."""

    def test_findings_for_an_unregistered_slug_are_refused(self, pg_registry):
        with pytest.raises(ValueError, match="no such project"):
            pg_registry.upsert_finding(
                "definitely_not_a_project", "ci_quality",
                [{"check_name": "x", "label": "y", "summary": "z"}])

    def test_metrics_for_an_unregistered_slug_are_refused(self, pg_registry):
        """The sibling guard, added after the FK pragma surfaced its absence."""
        with pytest.raises(ValueError, match="no such project"):
            pg_registry.upsert_metric(
                "definitely_not_a_project", "api_structure", {"symbol_count": 1})


class TestRemoveDeletesEveryChild:
    """registry.remove()'s own comment records five child tables it once failed
    to clean up — silently leaking orphans on SQLite, crashing on Postgres. On
    Postgres the FK is the assertion: if any child row survived, the parent
    DELETE would raise."""

    def test_remove_succeeds_with_children_in_every_table(self, pg_registry):
        slug = "pg_itest_cascade"
        pg_registry.add(Project(slug=slug, display_name="Cascade",
                                github_url="https://github.com/test/cascade",
                                collections=[]))
        pg_registry.upsert_file_inventory(slug, [("README.md", 10), ("src/a.py", 20)])
        pg_registry.upsert_dependencies(slug, [
            {"dep_name": "requests", "dep_version": "2.0", "ecosystem": "PyPI",
             "dep_type": "runtime", "source_file": "pyproject.toml"}])
        pg_registry.upsert_finding(slug, "ci_quality",
                                   [{"check_name": "c", "label": "pass", "summary": "s"}])
        pg_registry.upsert_metric(slug, "api_structure", {"symbol_count": 3})

        pg_registry.remove(slug)   # would raise on a missed child table

        assert pg_registry.get(slug) is None
        assert pg_registry.get_file_inventory(slug) == []
        assert pg_registry.query_dependencies(slug) == []


class TestPostgresStrictnessSqliteDoesNotHave:

    def test_history_group_by_runs_on_postgres(self, pg_registry, pg_project):
        """Postgres requires every selected column to be grouped or aggregated;
        SQLite does not. The history queries are the ones that GROUP BY, so
        they can parse and run on SQLite while being invalid Postgres — this
        executes them where that is actually checked."""
        pg_registry.upsert_metric(pg_project, "api_structure", {"symbol_count": 10},
                                  surveyed_at="2026-08-01T00:00:00")
        pg_registry.upsert_metric(pg_project, "api_structure", {"symbol_count": 20},
                                  surveyed_at="2026-08-02T00:00:00")
        history = pg_registry.query_metrics_history(pg_project, "api_structure", "symbol_count")
        assert [h["metric_value"] for h in history] == [10.0, 20.0]

    def test_a_non_numeric_metric_value_is_rejected(self, pg_registry, pg_project):
        """Static column types, the divergence no pragma can close: SQLite's
        dynamic affinity stores 'not-a-number' in a numeric column quite
        happily, Postgres refuses it."""
        with pytest.raises(Exception) as exc:
            with pg_registry._conn() as conn:
                conn.execute(
                    "INSERT INTO project_analysis_metrics "
                    "(project_slug, kind, surveyed_at, metric_name, metric_value) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (pg_project, "k", "2026-01-01", "m", "not-a-number"))
        assert "ValueError" not in type(exc.value).__name__


class TestRemoveCannotForgetANewTable:
    """The structural guard for the bug above.

    remove() hand-lists its child DELETEs, so every table added later is one
    someone has to remember. Twice now nobody did. Rather than trust the list,
    ask Postgres which tables actually carry a foreign key to projects.slug and
    require remove() to handle all of them — the same "compare the registry
    against the surface that must expose it" shape as
    tests/test_reachability_audit.py.
    """

    def test_remove_deletes_from_every_table_with_a_fk(self, pg_registry, pg_test_schema):
        with pg_registry._conn() as conn:
            rows = conn.execute("""
                SELECT tc.table_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.constraint_column_usage ccu
                  ON tc.constraint_name = ccu.constraint_name
                 AND tc.table_schema = ccu.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = %s
                  AND ccu.table_name = 'projects'
            """, (pg_test_schema,)).fetchall()
        referencing = sorted({r["table_name"] for r in rows})
        assert referencing, "no FKs found — the schema was not created as expected"

        import inspect
        from resource_explorer.registry import ProjectRegistry
        source = inspect.getsource(ProjectRegistry.remove)
        missing = [t for t in referencing if f"DELETE FROM {t} " not in source]
        assert not missing, (
            f"remove() does not delete from {missing}, which hold a foreign key to "
            "projects.slug — on Postgres removing a project with rows in those "
            "tables raises ForeignKeyViolation."
        )

    def test_rename_project_slug_covers_every_table_with_a_fk(self, pg_registry, pg_test_schema):
        """The same structural guard, for rename_project_slug()'s own
        enumeration (_PROJECT_SLUG_TABLES) — a table added later needs to be
        in both lists, and this catches the rename side forgetting one just
        as directly as the test above catches remove() forgetting one."""
        with pg_registry._conn() as conn:
            rows = conn.execute("""
                SELECT tc.table_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.constraint_column_usage ccu
                  ON tc.constraint_name = ccu.constraint_name
                 AND tc.table_schema = ccu.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = %s
                  AND ccu.table_name = 'projects'
            """, (pg_test_schema,)).fetchall()
        referencing = sorted({r["table_name"] for r in rows})
        assert referencing, "no FKs found — the schema was not created as expected"

        from resource_explorer.registry import ProjectRegistry
        missing = [t for t in referencing if t not in ProjectRegistry._PROJECT_SLUG_TABLES]
        assert not missing, (
            f"rename_project_slug()'s _PROJECT_SLUG_TABLES is missing {missing}, which "
            "hold a foreign key to projects.slug — a rename would leave those rows "
            "pointing at a slug that no longer exists."
        )


class TestRenameProjectSlugOnRealPostgres:
    """rename_project_slug()'s insert-new/update-children/delete-old
    ordering exists specifically to satisfy Postgres' FK enforcement
    (SQLite's PRAGMA mirrors it, but this is the backend the ordering was
    actually designed against)."""

    def test_rename_succeeds_with_fk_children_present(self, pg_registry):
        slug = "pg_itest_rename_src"
        new_slug = "pg_itest_rename_dst"
        for s in (slug, new_slug):
            existing = pg_registry.get(s)
            if existing:
                pg_registry.remove(s)
        pg_registry.add(Project(slug=slug, display_name="Rename Me",
                                github_url="https://github.com/test/rename-me",
                                collections=[]))
        pg_registry.upsert_file_inventory(slug, [("README.md", 10)])
        pg_registry.upsert_finding(slug, "ci_quality",
                                   [{"check_name": "c", "label": "pass", "summary": "s"}])

        try:
            pg_registry.rename_project_slug(slug, new_slug)  # would raise on FK violation
            assert pg_registry.get(slug) is None
            assert pg_registry.get(new_slug) is not None
            assert pg_registry.get_file_inventory(new_slug) == ["README.md"]
        finally:
            pg_registry.remove(new_slug) if pg_registry.get(new_slug) else None
            pg_registry.remove(slug) if pg_registry.get(slug) else None


class TestRegistryConstructionIsCheapOnAWarmProcess:
    """2026-09-29 per-request-latency investigation
    (PER-REQUEST-SERVER-LATENCY-IMPLEMENTED.md): every
    `ProjectRegistry()` construction re-ran `_init_schema` — ~150
    CREATE TABLE/ALTER TABLE/CREATE INDEX statements — against Postgres,
    measured directly at 250-470ms EVERY time, not just cold. Almost every
    web route does `registry = ProjectRegistry()` fresh, so this landed on
    every request. Fixed by caching the Engine and an "already verified"
    flag per `database_url` at the class level, so a process only pays this
    once per URL. These tests pin that behaviour directly against real
    Postgres rather than timing it, which would be flaky."""

    def test_second_construction_for_the_same_url_skips_schema_init(self, pg_test_schema, monkeypatch):
        from resource_explorer.config import get_config
        from resource_explorer.registry import ProjectRegistry

        cfg = get_config().pgvector
        url = (f"postgresql://{cfg.db_user}:{cfg.password}@{cfg.host}:{cfg.port}"
               f"/{cfg.dbname}?options=-csearch_path%3D{pg_test_schema}")

        # `pg_test_schema` is session-scoped (one throwaway schema, reused by
        # every integration test), so an earlier test in this same session
        # may already have constructed a `ProjectRegistry` against this exact
        # URL and populated the class-level cache. Clear this URL's entries
        # so "first ever construction" is deterministic regardless of test
        # order, rather than asserting the process-wide cache is untouched.
        ProjectRegistry._pg_engine_cache.pop(url, None)
        ProjectRegistry._pg_schema_ready.discard(url)

        calls = []
        real_init_schema = ProjectRegistry._init_schema

        def _counting_init_schema(self):
            calls.append(1)
            return real_init_schema(self)

        monkeypatch.setattr(ProjectRegistry, "_init_schema", _counting_init_schema)

        first = ProjectRegistry(database_url=url)
        second = ProjectRegistry(database_url=url)

        assert len(calls) == 1, "second construction re-ran _init_schema"
        assert first.engine is second.engine, "second construction opened its own pool"
        assert url in ProjectRegistry._pg_schema_ready


class TestDatabaseSurveysCacheIsInstanceScopedAndWriteInvalidated:
    """2026-09-29: profiling one board's `/survey-results` request showed
    `get_database_surveys` (fetches every historical survey's full
    `survey_data` blob) called 5 times for the same slug within a single
    request — `_credential_capability_results` deliberately searches every
    stored survey (its own docstring explains why), but nothing stopped
    five separate callers each re-running that same full fetch. Cached
    per-`ProjectRegistry`-instance; these tests pin the two properties that
    matter: repeat reads within one instance don't re-query, and a write
    through the same instance is never served stale."""

    @pytest.fixture
    def pg_surveyed_database(self, pg_registry):
        from resource_explorer.registry import DatabaseEntity

        slug = "pg_itest_survey_cache_db"
        if pg_registry.get_database(slug) is None:
            pg_registry.register_database(DatabaseEntity(
                slug=slug, display_name="Survey Cache Test DB", db_type="postgresql",
                host="localhost", port=5432, database_name=slug,
            ))
        yield slug
        pg_registry.remove_database(slug)

    def test_repeat_reads_within_one_instance_do_not_requery(self, pg_registry, pg_surveyed_database, monkeypatch):
        """The FULL fetch (every historical `survey_data` blob) happens once;
        the cheap freshness check (`_database_surveys_freshness`) is expected
        to run on every call, cache hit or miss — that is the point of the
        2026-09-29 self-invalidating-key hardening, not something to
        suppress. So this counts the full-fetch query specifically (its
        `ORDER BY surveyed_at DESC` clause, which the freshness query does
        not have), not every statement that mentions `database_surveys`."""
        from resource_explorer.registry import ConnectionWrapper

        pg_registry.record_database_survey(
            pg_surveyed_database, schema_count=1, table_count=1, column_count=1,
            survey_data={"credential_capability": {"visible": True}},
        )

        full_fetch_calls = []
        freshness_calls = []
        real_execute = ConnectionWrapper.execute

        def _counting_execute(self, sql, params=None):
            if "FROM database_surveys" in sql:
                if "ORDER BY surveyed_at DESC" in sql:
                    full_fetch_calls.append(1)
                elif "max(surveyed_at)" in sql:
                    freshness_calls.append(1)
            return real_execute(self, sql, params)

        monkeypatch.setattr(ConnectionWrapper, "execute", _counting_execute)

        first = pg_registry.get_database_surveys(pg_surveyed_database)
        second = pg_registry.get_database_surveys(pg_surveyed_database)

        assert len(full_fetch_calls) == 1, (
            "second get_database_surveys() call re-ran the full blob fetch"
        )
        assert len(freshness_calls) == 2, (
            "the cheap freshness check should run on every call, cache hit "
            "or miss — that's what makes the cache self-invalidating"
        )
        assert first == second

    def test_write_through_the_same_instance_is_not_served_stale(self, pg_registry, pg_surveyed_database):
        first = pg_registry.get_database_surveys(pg_surveyed_database)
        assert first == []
        pg_registry.record_database_survey(
            pg_surveyed_database, schema_count=1, table_count=2, column_count=3,
            survey_data={"credential_capability": {"visible": True}},
        )
        second = pg_registry.get_database_surveys(pg_surveyed_database)
        assert len(second) == 1, (
            "get_database_surveys served a cached empty result after a "
            "write through the same ProjectRegistry instance"
        )

    def test_get_latest_database_survey_uses_a_limit_1_query(self, pg_registry, pg_surveyed_database):
        """A dedicated query, not `get_database_surveys(...)[0]` — see that
        method's own docstring for why (profiled: 700-900ms vs ~30ms on a
        heavily-surveyed database)."""
        pg_registry.record_database_survey(
            pg_surveyed_database, schema_count=1, table_count=1, column_count=1,
            survey_data={"a": 1}, surveyed_at="2026-01-01T00:00:00",
        )
        pg_registry.record_database_survey(
            pg_surveyed_database, schema_count=2, table_count=2, column_count=2,
            survey_data={"b": 2}, surveyed_at="2026-01-02T00:00:00",
        )
        latest = pg_registry.get_latest_database_survey(pg_surveyed_database)
        assert latest["surveyed_at"] == "2026-01-02T00:00:00"


class TestGetDatabaseSurveysCacheSurvivesTheRunQueueWorkerPattern:
    """2026-09-29 hardening: the "Resource-explorer PR/CI merge" session's
    review of this branch found that the original per-instance survey cache
    (write-invalidated only through THAT SAME instance) is safe for web
    routes — every route constructs `ProjectRegistry()` fresh per request —
    but was a real staleness risk for any LONG-LIVED holder that constructs
    ONE `ProjectRegistry` and reuses it across many iterations, the shape
    `run_queue.py`'s `QueueRunner._loop` is written in: one
    `registry = ProjectRegistry()` before `while not self._stop.is_set():`,
    intended to be threaded through `claim_and_execute_once(registry) ->
    execute_run(row, registry=registry)` for every iteration. A survey
    written by a DIFFERENT process (the web server, or a different worker)
    in between two iterations would never invalidate that one instance's
    cached copy under the old scheme.

    This drives the REAL `run_queue.claim_and_execute_once` /
    `run_queue.execute_run` call chain — not two bare `ProjectRegistry`
    instances calling `get_database_surveys` directly in isolation — with
    ONE `ProjectRegistry` constructed once and threaded through multiple
    simulated run-queue iterations, exactly the way `QueueRunner._loop`
    does, so this proves the specific bug class (a long-lived worker
    registry serving a stale survey list after another process's write) is
    closed, not just the underlying cache mechanism in the abstract.

    Honesty note, found while building this test: `QueueRunner._loop`'s own
    `registry` local is currently DEAD — its call site
    (`claim_and_execute_once(kinds=self.kinds)`) does not pass `registry=`,
    so today's worker actually builds a fresh `ProjectRegistry` per
    iteration via `claim_and_execute_once`'s `registry or ProjectRegistry()`
    default, and even a future fix to pass it through would not, by itself,
    reach `get_database_surveys` — `execute_run` calls
    `HANDLERS[kind](target, result_ref)` with no `registry` argument at all,
    so every handler (`_handle_database_analysis_run` included) constructs
    its own fresh registry regardless. Neither of those is this fix's to
    make — they are separate, out-of-scope findings, logged in
    `PER-REQUEST-SERVER-LATENCY-IMPLEMENTED.md`'s
    staleness-hardening addendum. This test still exercises the call chain
    `claim_and_execute_once`/`execute_run` genuinely support (both accept an
    explicit `registry=`) with one instance reused across iterations, which
    is the pattern the cache must be safe under whether today's wiring
    happens to reach it or a future fix completes that wiring.
    """

    def test_worker_registry_sees_a_survey_written_by_a_different_instance_between_runs(
        self, pg_test_schema, monkeypatch
    ):
        from resource_explorer.config import get_config
        from resource_explorer.registry import DatabaseEntity, ProjectRegistry
        from resource_explorer import run_queue as rq

        cfg = get_config().pgvector
        url = (f"postgresql://{cfg.db_user}:{cfg.password}@{cfg.host}:{cfg.port}"
               f"/{cfg.dbname}?options=-csearch_path%3D{pg_test_schema}")

        # ONE ProjectRegistry, constructed once — exactly QueueRunner._loop's
        # `registry = ProjectRegistry()` before its `while` loop — reused
        # across multiple simulated iterations below, instead of letting
        # each call build its own.
        worker_registry = ProjectRegistry(database_url=url)

        slug = "pg_itest_worker_loop_survey_cache"
        worker_registry.register_database(DatabaseEntity(
            slug=slug, display_name="Worker Loop Test DB", db_type="postgresql",
            host="localhost", port=5432, database_name=slug,
        ))
        try:
            # Iteration 1: a read against the slug through the long-lived
            # instance — the same shape db_derived.py's _snapshot_keys uses
            # (registry.get_database_surveys(slug)) when an analysis run
            # reaches it via this same worker registry.
            first_reads = worker_registry.get_database_surveys(slug)
            assert first_reads == []

            # Iteration 1's actual unit of work: an unrelated row claimed
            # and executed through the REAL run_queue call chain
            # (claim_and_execute_once -> execute_run), on the SAME threaded
            # registry instance — not a direct get_database_surveys() call
            # standing in for it.
            monkeypatch.setitem(
                rq.HANDLERS, "curate_commit",
                lambda target, result_ref: rq.RunOutcome(state="succeeded"),
            )
            run_id = worker_registry.enqueue_run("curate_commit", {"curation_id": "noop"}, requested_by="itest-curator")
            claimed = rq.claim_and_execute_once(worker_registry)
            assert claimed is not None and claimed["id"] == run_id
            assert worker_registry.get_run(run_id)["state"] == "succeeded"

            # A DIFFERENT process — the web server, or a different worker —
            # writes a new survey for the SAME slug in between iterations,
            # through its OWN ProjectRegistry instance.
            other_process_registry = ProjectRegistry(database_url=url)
            other_process_registry.record_database_survey(
                slug, schema_count=1, table_count=2, column_count=3,
                survey_data={"credential_capability": {"visible": True}},
            )

            # Iteration 2, same long-lived worker registry: another
            # unrelated row runs through the same call chain first (to keep
            # exercising claim_and_execute_once/execute_run across
            # iterations, matching QueueRunner._loop's repeated-call shape)...
            run_id_2 = worker_registry.enqueue_run("curate_commit", {"curation_id": "noop-2"})
            claimed_2 = rq.claim_and_execute_once(worker_registry)
            assert claimed_2 is not None and claimed_2["id"] == run_id_2

            # ...and THEN the very next read for the original slug, through
            # the SAME instance used in iteration 1, must see the newer
            # survey — not the empty list it cached back in iteration 1.
            second_reads = worker_registry.get_database_surveys(slug)
            assert len(second_reads) == 1, (
                "the long-lived run-queue worker registry served a stale "
                "cached survey list after a DIFFERENT process's write — "
                "exactly the 2026-09-29 staleness bug this hardening closes"
            )
        finally:
            worker_registry.remove_database(slug)


class TestFindLatestDatabaseSurveyWithKey:
    """2026-09-29 round 2 (PER-REQUEST-SERVER-LATENCY-ROUND-2-IMPLEMENTED.md): `find_latest_database_survey_with_key` replaces
    the "fetch every historical blob, loop in Python" pattern with a
    server-side `jsonb_exists` containment query on Postgres. This is the
    exact search shape the 2026-09-26 fix (`a0f28aec`, "Fix schema-count
    leading number and credential-capability key mismatch") landed —
    `_credential_capability_results` searching every stored survey
    newest-first rather than only the latest — so this class exercises
    that shape directly against real Postgres, not just the
    `_FakeRegistry`-level unit test in test_schema_inventory_headline.py."""

    @pytest.fixture
    def pg_surveyed_database(self, pg_registry):
        from resource_explorer.registry import DatabaseEntity

        slug = "pg_itest_find_survey_with_key_db"
        if pg_registry.get_database(slug) is None:
            pg_registry.register_database(DatabaseEntity(
                slug=slug, display_name="Find Survey With Key Test DB", db_type="postgresql",
                host="localhost", port=5432, database_name=slug,
            ))
        yield slug
        pg_registry.remove_database(slug)

    def test_returns_none_when_no_survey_has_the_key(self, pg_registry, pg_surveyed_database):
        pg_registry.record_database_survey(
            pg_surveyed_database, schema_count=1, table_count=1, column_count=1,
            survey_data={"schema_count": 1},
        )
        assert pg_registry.find_latest_database_survey_with_key(
            pg_surveyed_database, "credential_capability") is None

    def test_finds_the_key_on_the_only_survey(self, pg_registry, pg_surveyed_database):
        pg_registry.record_database_survey(
            pg_surveyed_database, schema_count=1, table_count=1, column_count=1,
            survey_data={"credential_capability": {"schema_total": 8, "schema_visible": 6}},
        )
        found = pg_registry.find_latest_database_survey_with_key(
            pg_surveyed_database, "credential_capability")
        assert found is not None
        assert json.loads(found["survey_data"])["credential_capability"]["schema_total"] == 8

    def test_the_2026_09_26_bug_shape_an_older_survey_carries_the_key_the_newest_does_not(
        self, pg_registry, pg_surveyed_database,
    ):
        """The exact real-world shape found live 2026-09-26 on `coco_pharma`:
        an older survey ran the credential_capability probe; a later,
        newer survey was schema/statistics-only and carries no such key at
        all (not merely a falsy one — the key is genuinely absent, exactly
        as `DatabaseSurveyor.survey()` only ever sets it when the probe
        step actually ran). The newest-first search must not stop at the
        newer, probe-less survey and report "nothing" — it must keep
        looking and find the older reading, same as `_credential_capability_
        results`'s own docstring requires and `a0f28aec` fixed live for."""
        pg_registry.record_database_survey(
            pg_surveyed_database, schema_count=8, table_count=61, column_count=200,
            survey_data={"credential_capability": {"schema_total": 8, "schema_visible": 6,
                                                     "table_total": 61, "table_select": 3}},
            surveyed_at="2026-09-25T00:00:00",
        )
        pg_registry.record_database_survey(
            pg_surveyed_database, schema_count=8, table_count=61, column_count=200,
            survey_data={"schema_count": 8},  # newer, no probe this run
            surveyed_at="2026-09-26T00:00:00",
        )
        found = pg_registry.find_latest_database_survey_with_key(
            pg_surveyed_database, "credential_capability")
        assert found is not None
        assert found["surveyed_at"] == "2026-09-25T00:00:00"
        cap = json.loads(found["survey_data"])["credential_capability"]
        assert cap["schema_total"] == 8
        assert cap["schema_visible"] == 6

    def test_an_empty_dict_value_is_treated_as_falsy_and_search_continues(
        self, pg_registry, pg_surveyed_database,
    ):
        """The key existing with a falsy value (`{}`) is the one case the
        fast SQL path alone cannot distinguish from a truthy one — pinned
        directly per `find_latest_database_survey_with_key`'s own docstring
        ("Candidate had the key but a falsy value... fall through to the
        exhaustive scan"). Not expected in real surveyor output (the probe
        either omits the key or writes a real reading), but correctness
        here must not depend on that assumption holding."""
        pg_registry.record_database_survey(
            pg_surveyed_database, schema_count=1, table_count=1, column_count=1,
            survey_data={"credential_capability": {"schema_total": 3, "schema_visible": 3}},
            surveyed_at="2026-09-25T00:00:00",
        )
        pg_registry.record_database_survey(
            pg_surveyed_database, schema_count=1, table_count=1, column_count=1,
            survey_data={"credential_capability": {}},  # present, empty — falsy
            surveyed_at="2026-09-26T00:00:00",
        )
        found = pg_registry.find_latest_database_survey_with_key(
            pg_surveyed_database, "credential_capability")
        assert found is not None
        assert found["surveyed_at"] == "2026-09-25T00:00:00", (
            "the newer survey's empty credential_capability was treated as "
            "truthy — the fast path's candidate check must verify, not just "
            "check key presence"
        )

    def test_credential_capability_results_reader_uses_the_fast_path_correctly(
        self, pg_registry, pg_surveyed_database,
    ):
        """End to end through the actual results reader
        (`_credential_capability_results`), not just the registry method —
        confirms the reader's `find_latest_database_survey_with_key`
        integration returns the same answer the pre-round-2 linear scan
        would have, for the exact bug shape `a0f28aec` fixed."""
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _credential_capability_results,
        )

        pg_registry.record_database_survey(
            pg_surveyed_database, schema_count=8, table_count=61, column_count=200,
            survey_data={"credential_capability": {"schema_total": 8, "schema_visible": 6}},
            surveyed_at="2026-09-25T00:00:00",
        )
        pg_registry.record_database_survey(
            pg_surveyed_database, schema_count=8, table_count=61, column_count=200,
            survey_data={"schema_count": 8},
            surveyed_at="2026-09-26T00:00:00",
        )
        cap = _credential_capability_results(pg_registry, pg_surveyed_database)
        assert cap == {"schema_total": 8, "schema_visible": 6}

    def test_repeat_calls_within_one_instance_do_not_requery(self, pg_registry, pg_surveyed_database, monkeypatch):
        """A single board read calls `_credential_capability_results` (and
        so this method) from several distinct call sites for the same
        slug/key — 5 times, per `cProfile` on `schema_inventory`. Cached
        per `(slug, key)` on the `ProjectRegistry` instance, lazily and
        deliberately WITHOUT touching `__init__`/`record_database_survey`
        (see this method's own docstring for why) — pinned here the same
        way `get_database_surveys`'s own dedup is pinned, by counting real
        `database_surveys` queries across repeat calls."""
        from resource_explorer.registry import ConnectionWrapper

        pg_registry.record_database_survey(
            pg_surveyed_database, schema_count=1, table_count=1, column_count=1,
            survey_data={"credential_capability": {"schema_total": 3, "schema_visible": 3}},
        )

        calls = []
        real_execute = ConnectionWrapper.execute

        def _counting_execute(self, sql, params=None):
            if "FROM database_surveys" in sql:
                calls.append(1)
            return real_execute(self, sql, params)

        monkeypatch.setattr(ConnectionWrapper, "execute", _counting_execute)

        first = pg_registry.find_latest_database_survey_with_key(
            pg_surveyed_database, "credential_capability")
        second = pg_registry.find_latest_database_survey_with_key(
            pg_surveyed_database, "credential_capability")

        assert len(calls) == 1, "second find_latest_database_survey_with_key() call re-queried Postgres"
        assert first == second


class TestFindLatestDetailSurveyedAt:
    """2026-09-29 round 3 (PER-REQUEST-SERVER-LATENCY-ROUND-3-IMPLEMENTED.md): `find_latest_detail_surveyed_at` replaces
    `db_derived.py`'s `_resolve_table_surveyed_at` walk (one `query_detail_
    rows` call per candidate `surveyed_at`, newest first, until one is
    non-empty) with a single `MAX(surveyed_at)` query — provably
    equivalent per that method's own docstring, pinned here directly
    against real Postgres."""

    @pytest.fixture
    def pg_database(self, pg_registry):
        from resource_explorer.registry import DatabaseEntity

        slug = "pg_itest_find_latest_detail_db"
        if pg_registry.get_database(slug) is None:
            pg_registry.register_database(DatabaseEntity(
                slug=slug, display_name="Find Latest Detail Test DB", db_type="postgresql",
                host="localhost", port=5432, database_name=slug,
            ))
        yield slug
        pg_registry.remove_database(slug)

    def test_returns_none_when_the_table_has_no_rows_for_this_slug(self, pg_registry, pg_database):
        assert pg_registry.find_latest_detail_surveyed_at("database_tables", pg_database) is None

    def test_finds_the_only_surveyed_at(self, pg_registry, pg_database):
        pg_registry.write_detail_rows(
            "database_tables", pg_database, "2026-09-29T00:00:00", "local",
            [{"schema_name": "public", "table_name": "t1"}],
        )
        at = pg_registry.find_latest_detail_surveyed_at("database_tables", pg_database)
        assert at == "2026-09-29T00:00:00"

    def test_finds_the_newest_of_several_surveyed_ats(self, pg_registry, pg_database):
        for at in ("2026-09-27T00:00:00", "2026-09-29T00:00:00", "2026-09-28T00:00:00"):
            pg_registry.write_detail_rows(
                "database_tables", pg_database, at, "local",
                [{"schema_name": "public", "table_name": "t1"}],
            )
        found = pg_registry.find_latest_detail_surveyed_at("database_tables", pg_database)
        assert found == "2026-09-29T00:00:00"

    def test_a_run_that_never_touched_this_table_is_correctly_skipped(self, pg_registry, pg_database):
        """The exact property `_resolve_table_surveyed_at`'s walk existed
        for: a later run's own steps can genuinely never touch a structured
        table at all (a schema-only run writes no `database_table_activity`
        rows), and that must not shadow an earlier run that DID."""
        pg_registry.write_detail_rows(
            "database_table_activity", pg_database, "2026-09-27T00:00:00", "local",
            [{"schema_name": "public", "table_name": "t1", "rows_inserted": 5}],
        )
        # A later survey ran, but this particular table was never touched by
        # it — no row exists for "2026-09-29" in database_table_activity at all.
        found = pg_registry.find_latest_detail_surveyed_at("database_table_activity", pg_database)
        assert found == "2026-09-27T00:00:00"

    def test_require_any_non_null_skips_a_row_with_every_counter_null(self, pg_registry, pg_database):
        """The `database_table_activity`-specific case `require_measured_
        counter` exists for: a row can be written (the step ran) with every
        counter NULL (nothing was actually measured). That must not count
        as "this run has the answer" — the newer, unmeasured row must be
        skipped in favour of the older, real one."""
        pg_registry.write_detail_rows(
            "database_table_activity", pg_database, "2026-09-27T00:00:00", "local",
            [{"schema_name": "public", "table_name": "t1", "rows_inserted": 5}],
        )
        pg_registry.write_detail_rows(
            "database_table_activity", pg_database, "2026-09-29T00:00:00", "local",
            [{"schema_name": "public", "table_name": "t1"}],  # every counter NULL
        )
        found = pg_registry.find_latest_detail_surveyed_at(
            "database_table_activity", pg_database,
            require_any_non_null=("rows_inserted", "rows_updated", "rows_deleted",
                                   "seq_scan", "idx_scan"),
        )
        assert found == "2026-09-27T00:00:00", (
            "the newer, all-NULL-counter row was accepted as measured"
        )

    def test_resolve_table_surveyed_at_gives_identical_answers_to_the_old_walk(
        self, pg_registry, pg_database,
    ):
        """End to end through `db_derived._resolve_table_surveyed_at` itself
        (not just the registry method it now calls) — the exact bug shape
        the walk existed for, reproduced: an older run wrote
        `database_table_activity` rows with real counters; a newer run
        wrote NONE for this table at all (a schema-only run). The newer
        run's absence here must not shadow the older run's real answer."""
        from resource_explorer.surveyors.database.db_derived import _resolve_table_surveyed_at

        pg_registry.write_detail_rows(
            "database_table_activity", pg_database, "2026-09-27T00:00:00", "local",
            [{"schema_name": "public", "table_name": "t1", "rows_inserted": 5}],
        )
        at, rows = _resolve_table_surveyed_at(
            pg_registry, pg_database, "database_table_activity", None,
            require_measured_counter=True,
        )
        assert at == "2026-09-27T00:00:00"
        assert len(rows) == 1
        assert rows[0]["rows_inserted"] == 5
