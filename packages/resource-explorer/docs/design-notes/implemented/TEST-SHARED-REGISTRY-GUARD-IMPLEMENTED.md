# Test guard: no pytest test may reach the shared registry (implemented 2026-10-02)

## Incident

A new test file set `REGISTRY_DATABASE_URL` (a temp SQLite file) in a fixture.
`resource_explorer.config.get_config()` had already been cached, so the env var
was ignored and the test opened the shared development Postgres
(`localhost:5442`, database `egeria_advisor`, schema `resource_explorer`), ran
the schema init and inserted two fake rows (`db_a`, `db_b`) into the real
`databases` table. `list_databases()` broke for the whole app until the rows
were deleted by hand.

## What was added

- `tests/shared_registry_guard.py`, installed from the top of `tests/conftest.py`
  at import (before conftest's own reachability probe). Two hooks, both at the
  connection itself, so a cached config cannot defeat them:
  - SQLAlchemy `do_connect` event on the `Engine` class: covers every engine in
    the process (registry, feedback store, metrics collector, leader election,
    Prefect flows), immediately before the DBAPI connect. Strict.
  - A `psycopg2.connect` wrapper for direct connections. Lenient (see below).
- `reset_cached_config` autouse fixture in conftest: `config._config = None`
  before and after every test, so a per-test `monkeypatch.setenv` is honoured.
- `tests/test_shared_registry_guard.py`: guard tests using a FAKE connect only.

## What counts as the shared registry

Derived from the config classes' declared defaults (`PgVectorConfig`,
`RegistryConfig`), never from the cached config or env, which is what failed:
a loopback host, port 5442, database `egeria_advisor`, with `search_path` other
than a throwaway test schema (`resource_explorer_test*`).

- SQLAlchemy path: a URL with no `search_path` also counts as shared.
- Raw `psycopg2.connect` with no `search_path` is allowed: conftest's probe and
  `pg_test_schema` fixture connect that way to create and drop the throwaway
  schema, and `--corpus` tests read through it. This is a known gap: a raw
  connection that then writes to the real schema by qualified name is not
  caught. The registry path, where the damage happened, is closed.

A refusal raises `SharedRegistryAccessError` naming the test
(`PYTEST_CURRENT_TEST`) and the opt-in.

## Opt-in and CI

- `RE_TESTS_ALLOW_SHARED_REGISTRY=1` lets a deliberate run through. Nobody sets
  it by default; coordinate with live peers first.
- CI (`.github/workflows/resource-explorer.yml`) is exempt via
  `GITHUB_ACTIONS=true`. Its Postgres service container uses the same
  `localhost:5442 / egeria_advisor / resource_explorer` coordinates as the dev
  instance and sets `REGISTRY_DATABASE_URL` to the real schema name, so the
  address alone cannot tell them apart; the container is created empty for the
  run and discarded. The workflow file is unchanged. Per-test schemas
  (`pg_registry`, `pg_store`) on a developer machine pass on their own, since
  their schema starts with `resource_explorer_test`.

## Not verified

A real CI run. The exemption relies on GitHub setting `GITHUB_ACTIONS=true`.

## Addendum 2026-10-05: registry open log, CLI line, builder checklist

Trigger: a builder command in a worktree (no `.env`, so the default shared
Postgres) created `catalogue_commit_proofs` in the shared registry. The test
guard did not apply because it was a command, not a test.

- `resource_explorer/registry_label.py`: `describe_registry(url)` returns
  `registry: localhost:5442/egeria_advisor (shared) schema=resource_explorer`,
  `registry: host:port/db schema=...` or `registry: sqlite:///<basename>`.
  Host and database only; no user, password, query string or directory.
- `ProjectRegistry._log_open`: one INFO line per distinct registry URL per
  process (servers build a registry per request, so not per construction or
  query). Also fixed the pre-existing `registry_init` timing line, which logged
  the full `database_url` INCLUDING the default password; it now logs the label.
- CLI root callback prints the same line once per invocation to stderr (stdout
  stays clean for piping). It prints for every command, including ones that
  never open the registry. Not a refusal: 8810 and 8813 use the default.
- New guard test `test_default_config_without_env_file_fails_closed`: no env var,
  `.env` disabled, `ProjectRegistry()` with no arguments reaches the connect
  hook and is refused before schema migration (FAKE connect; never real).
  Red-run with the guard's `check()` disabled: 5 guard tests fail, including it.
- Tests: `tests/test_registry_open_log.py`, `tests/test_cli_registry_line.py`.
- Docs: `docs/BUILDER-BRIEF-CHECKLIST.md` and the same line in the package CLAUDE.md Setup (no file citation there: the design-note reference test treats an uppercase .md name as a design note).

Known limit, unchanged: the guard only protects pytest. A non-test command in a
worktree is only made visible (the `registry:` line), not blocked.

## Addendum 2026-10-05: PGVECTOR schema gap closed

The "known gap" above (a raw connection that writes the real schema by qualified
name) is closed for the one case that mattered: a `PgVectorStore` built on the
DEFAULT (shared) schema.

Audit table row: PGVECTOR_* raw psycopg2 vector store, default schema
`resource_explorer` -> COVERED.

- `tests/shared_registry_guard.py`: `install()` wraps `PgVectorStore.__init__`
  (construction only builds config, never connects) and `check_vector_schema`
  raises `SharedRegistryAccessError` naming `PGVECTOR_SCHEMA` and the test when
  the resolved schema equals `PgVectorConfig`'s declared default. An explicit
  `schema="resource_explorer"` is refused the same way. Any other name is a
  test's own override and is left alone. `RE_TESTS_ALLOW_SHARED_REGISTRY=1` and
  `GITHUB_ACTIONS=true` exempt it, as for the connect guard.
- `tests/conftest.py`: autouse `isolate_pgvector_schema` sets `PGVECTOR_SCHEMA`
  to `resource_explorer_test_<pid>_v<n>` per test. It is lazy: the env var
  connects to nothing; the store creates the schema itself on connect, and the
  fixture drops it at teardown only if a store was actually built on it and
  pgvector is reachable. The orphan sweeper now also recognises `<pid>_v<n>`.
- The raw-psycopg2 allowance for the setup connection (`pg_test_schema`) stays.
- Tests: `tests/test_shared_registry_guard.py` (FAKE connect only; refusal,
  explicit arg, scratch name, own override, opt-in/CI exemptions).

Not run by the builder (pgvector unreachable, PGVECTOR_PORT=1): per-test scratch
schema creation and DROP at teardown against a real Postgres, and a real default
store connecting under the scratch schema. The full suite on a pgvector-reachable
machine/CI verifies them.

## Addendum 2026-10-05 (2): the metrics leak, setting audit, isolation fixture

PR/CI's full suite refused `test_integration_pgvector.py::TestEndToEndIntegration::test_ingest_then_query_returns_real_retrieved_content`:
`RAGSystem._init_observability` opens a `MetricsCollector`, whose
`METRICS_DATABASE_URL` is a separate setting that also defaults to the shared
Postgres. The test patched the registry and vector store, not it. The shared
`query_log` held 1381 rows, 1379 of them `fixtureproj`: every local run with
pgvector reachable wrote there. Those rows were NOT touched; the owner decides.

| Setting | Default | Guard covers it | Fix |
|---|---|---|---|
| `REGISTRY_DATABASE_URL` (registry; leader election and Prefect flows read it) | shared 5442/egeria_advisor, schema resource_explorer | yes (SQLAlchemy connect, strict) | conftest `isolate_database_url_settings` |
| `METRICS_DATABASE_URL` (`MetricsCollector`) | same | yes | same fixture; the integration test also sets it explicitly |
| `FEEDBACK_DATABASE_URL` (`FeedbackStore`) | same | yes | same fixture |
| `PGVECTOR_HOST/PORT/DBNAME/SCHEMA` (`PgVectorStore`, raw psycopg2) | localhost:5442/egeria_advisor, schema resource_explorer | NO: raw psycopg2 without `search_path` is deliberately allowed (conftest's probe and `pg_test_schema` connect that way), and the store qualifies tables by schema name | not isolated by the fixture: it would break the pgvector integration tier. Tests use `PgVectorStore(schema=pg_test_schema)`; a test that builds a default `PgVectorStore()` with pgvector reachable still reaches the shared schema. OPEN GAP. |
| `PREFECT_API_URL` | http://localhost:4200 | n/a (HTTP, not Postgres) | none |

- The refusal message now names the setting, read from the calling module
  (`metrics_collector` -> `METRICS_DATABASE_URL`, `feedback_store` ->
  `FEEDBACK_DATABASE_URL`, `registry`/`leader_election` -> `REGISTRY_DATABASE_URL`),
  because the three share one default and the address cannot tell them apart.
- `isolate_database_url_settings` (autouse, conftest) sets each of the three to a
  per-test temp SQLite file when it is unset or already addresses the shared
  registry. A deliberate override is left alone, and CI (`GITHUB_ACTIONS=true`)
  is untouched. A test overrides with its own `monkeypatch.setenv`.
- Tests: `test_every_shared_default_url_setting_is_isolated_by_conftest`
  (red without the fixture), `test_refusal_names_the_metrics_setting`,
  `test_feedback_store_default_is_refused_and_named`, `test_setting_hint_reads_the_calling_module`.
- Not verified: the integration test itself, which skips without a reachable
  pgvector here. Its fix is covered by the isolation test plus the metrics
  refusal test; a run with pgvector reachable is still needed.

## Addendum 2026-10-05 (3): regression in 26fb2057, and the check that catches it

The METRICS_DATABASE_URL block from the previous addendum landed in
`TestVectorStoreIntegration::test_multi_collection_store_wrapper_real_round_trip`,
which does not request `tmp_path`, instead of the e2e test that does. It
raised NameError only where pgvector is reachable; the test skips elsewhere,
so every local run passed. Fixed: that test is byte-identical to origin/main
again and the block is in `test_ingest_then_query_returns_real_retrieved_content`.

Guard against the class: `tests/test_no_undefined_names_in_tests.py` runs
`ruff check --select F821` over `tests/` (skips if ruff is absent) and proves
it catches an unrequested-fixture name. Two pre-existing false positives in
quoted annotations were cleaned (`Path` in test_ingestion.py, `ast` in
test_vendored.py) by importing the names at module level.

## Addendum 2026-10-05 (4): CI-vs-local dependence in the isolation test

`test_every_shared_default_url_setting_is_isolated_by_conftest` asserted sqlite
unconditionally, but the fixture is deliberately a no-op under
`GITHUB_ACTIONS=true`, so CI (service-container URL) failed it. The decision
now lives in `shared_registry_guard.isolate_url_settings(environ, setenv,
tmp_dir)`, a pure function the autouse fixture calls. Tests drive it with a
fake environment, so they do not depend on the ambient flag: outside CI unset
and shared URLs are replaced and a deliberate override is kept; under CI nothing
is replaced. `test_autouse_fixture_applies_the_isolation_outside_ci` checks the
real fixture's effect and skips under CI. The guard was not loosened. Swept the
other tests added in this PR for the same dependence: none assert isolation
without regard to the flag (the fake-connect tests clear `GITHUB_ACTIONS`).

## Addendum 2026-10-05 (5): the live Egeria tiers are opt-in

Until now the live READ tier (`requires_egeria`, the narrower `live_egeria`
marker; `test_egeria_live_smoke.py`, `TestAgainstLiveEgeria`,
`test_sub_resource_templates.py`) ran whenever a 2 s probe reached the
platform, so every full suite from any worktree talked to the shared dev
Egeria as the configured user (steady refusals in the platform log).

Rule now, decided by pure functions in `tests/live_egeria_tier.py`:

| Run | Reads | Writes |
|---|---|---|
| default | skipped: "live Egeria tier is opt-in: run with --live-egeria-reads (or RE_LIVE_EGERIA_READS=1) after a peer round"; NO probe is made | skipped |
| `--live-egeria-reads` / `RE_LIVE_EGERIA_READS=1` | run if reachable | skipped |
| `--live-egeria-writes` without `RE_LIVE_EGERIA_WRITES_CLEARED` (missing or blank) | run (writes imply reads) | skipped, not failed: "live Egeria writes need RE_LIVE_EGERIA_WRITES_CLEARED=<who>/<UTC time> naming the peer round" |
| `--live-egeria-writes` + non-empty clearance | run | run |
| `GITHUB_ACTIONS=true` | always skipped | always skipped |

- The reachability probe moved from import time to a lazy call made only when a
  tier is opted in, so a default run does not contact the platform at all.
- Session header (`pytest_report_header`), only when reads are ON:
  `live Egeria tier: <platform url> as <user> · reads ON`, or with writes
  `... · writes ON · cleared by <value>`. URL and user id only; never a password or token.
- `live_egeria_write_target` also refuses (skips) without the clearance, prints
  `live Egeria write: cleared by <value>`, records it as the test property
  `live_egeria_writes_cleared_by` (appears in the junit/report) and on
  `LiveEgeriaWriteTarget.cleared_by`.
- No live test's assertions were changed; only whether they run. The existing
  gate function `_live_egeria_writes_should_skip` and its tests are unchanged.
- Tests: `tests/test_live_egeria_tier.py`, fake environment and fake items.
- Another session's done is a statement about itself, never a clearance from the others; the peer round is a question to every live peer, and the answer set is what goes in the variable.
