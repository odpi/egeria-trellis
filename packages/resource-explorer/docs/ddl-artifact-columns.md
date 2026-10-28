# DDL record: artifacts slice-1 columns (re/artifact-columns)

**Status:** branch, not deployed. This is the deployment record for three additive columns in RE's shared
registry. **Decision (project owner, 2026-10-15):** go on the artifacts slice-1 DDL columns, which are
`survey_commit_sha` per run and `file_role` per symbol and marker row from one shared classifier
(DESIGN-ARTIFACTS-VIEW-AND-DOC-LINKS.md §3, §7.1).

Nothing in this branch has run against the shared registry (localhost:5442). The columns are added only by
RE's own schema init, the first time a process running this code opens the shared registry, and only after the
owner's word for that restart. Merging is not enough.

## The exact Postgres DDL

`ProjectRegistry._migrate_artifact_columns` in `resource_explorer/registry.py` runs these at the end of
`_init_schema`, after the main init transaction has committed. **Each ALTER runs in its own short
transaction** (one `with self._conn()` per table), and only when `_get_table_columns` says the column is
missing:

```sql
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE project_code_symbols ADD COLUMN IF NOT EXISTS file_role TEXT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE project_code_markers ADD COLUMN IF NOT EXISTS file_role TEXT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE step_runs ADD COLUMN IF NOT EXISTS survey_commit_sha TEXT; COMMIT;
```

On SQLite the same statements run without `IF NOT EXISTS`.

### Lock behaviour

The columns are nullable with no default. On Postgres 11 and later that is a catalog-only change, with no
table rewrite. It still needs an ACCESS EXCLUSIVE lock on the table, and the effects are these:

- **Readers can be blocked.** While the ALTER waits for its lock, every new reader of that table queues behind
  it. For example, a symbol drill-down queues behind the `project_code_symbols` ALTER. Each wait is bounded at
  10 s. Because each ALTER commits on its own, one table's lock is never held while the next table's is
  awaited. In the worst case one reader waits about 10 s. Under the earlier single-transaction design the
  symbols lock could be held across the later waits, for about 20 s or more.
- **Startup fails cleanly on a timeout.** At 10 s that ALTER's transaction rolls back and startup raises
  RuntimeError "Could not add <table>.<column>: another process holds a lock …; stop other Resource Explorer
  processes and restart." Columns already added in earlier transactions stay, and they are correct.
  `tests/test_artifact_columns.py::test_a_held_table_lock_fails_the_start_cleanly_and_a_later_start_adds_the_column`
  pins this.
- **The cure is to restart when the table is quiet:** no survey running, and the Prefect worker idle.
- **Concurrent starts are safe.** Two processes that both see a column missing both ALTER. The second waits on
  the first's lock, and `IF NOT EXISTS` then turns its ALTER into a no-op.
  `test_two_registries_adding_the_columns_at_the_same_moment_both_succeed` pins this with two threads and a
  barrier.

Older rows read NULL, and no data is migrated.

| Column | Holds | NULL means |
|---|---|---|
| `project_code_symbols.file_role` | `core`, `test`, `generated`, `vendored` or `docs` | extracted before the column existed |
| `project_code_markers.file_role` | the same | the same |
| `step_runs.survey_commit_sha` | the commit a repo step run surveyed | the run read no code, the zipball's SHA could not be established, not a repo run, or the row predates the column |

## Which code writes and reads them

**Writes.**
- `ingestion/file_role.py` `classify_file_role(path)` is the one classifier. It calls
  `ingestion/vendored.provenance` for vendored and generated paths, `ingestion/test_paths.is_test_path` for tests,
  and checks for a `docs/`, `doc/` or `documentation/` directory outside a `src/` root.
- `CodeSymbolExtractor.extract` and `extract_markers` (`ingestion/code_symbol_extractor.py`) stamp `file_role` on
  every row they return. Every extraction path goes through them: the survey step `repo_symbol_extraction`
  (`SymbolExtractionSurveyor`), RAG ingestion and `refresh_profile` (`ingestion/pipeline.py`).
- `ProjectRegistry.upsert_code_symbols` writes the role, and a re-extraction overwrites it.
  `upsert_code_markers` also writes the role.
- `step_runs.survey_commit_sha` is the commit of the code the run READ. `GitHubClient.zipball_root` and
  `git_clone_root` set `last_served_sha`: the SHA the zipball is cached under, or the clone's
  `git rev-parse HEAD`. The uncached zipball fallback, whose SHA is unknown, sets None. `_acquire_zipball_root` and
  `_acquire_git_clone_root` write that SHA into the run's `served` dict, once per run, when
  `resolve_resources` acquires them. `SurveyOrchestrator.run` sets `SurveyResult.survey_commit_sha` and passes it
  to `step_cost_observer.record` for every step_runs row of the run, auto-run prerequisites included. The
  runners `_run_step` and `_run_batch` return it, so the executor's own `_record_cost` rows carry it too.
- A run that read no code, such as API-only steps or a caller-supplied `--data-path`, stores NULL. The column is
  never filled from `projects.last_commit_sha`, which moves only on a re-index and can be older than the tree that
  was read (coordinator ruling, 2026-10-15).

**Reads.**
- `artifact_provenance.py` provides `role_label`, `commit_label` and `extraction_commit` (the latest
  `repo_symbol_extraction` step run's `survey_commit_sha`). NULL reads "not recorded".
- `members._symbols_in_file` shows each symbol's role. `members._symbol_members` adds "Symbols extracted at
  commit <sha12>", or "Extraction commit not recorded".

**Rules the review added (2026-10-15).**
- A zipball cache miss downloads `zipball/<sha>`, the SHA it is cached and recorded under, not the branch. A
  clone records its own `git rev-parse HEAD`.
- Only a step that **completed** carries the SHA. A step that raised, or reported `step_error`, stores NULL
  (`step_cost_observer.completed`).
- `artifact_provenance.extraction_commit` reads the latest **successful** extraction, meaning one whose
  `metrics.outcomes` has a completed outcome, so a failed run after a good one does not hide the good one's
  commit.
- Prefect rows (`prefect/flows._record_step_cost`) take the SHA from the runner output, as the local executor
  does.

## Which processes would create it

These columns are added by the first process running this code that constructs a `ProjectRegistry` against the
shared registry. The creators are the same five as for `step_annotations` (`docs/ddl-step-annotations.md`):

1. 8813, the gate server, on its restart. This is the intended creator.
2. 8810, `--reload` on the main checkout. Do not fast-forward the main checkout before the owner's word.
3. The Prefect worker, on its next start.
4. Any CLI or `re-py.sh` probe run from a checkout that carries the change.
5. Test runs, but only in their own throwaway schema.

No script or SQL file outside `registry.py` alters these three tables.

## BEFORE snapshot: `~/.re-gate/scripts/ddl_artifact_columns_snapshot.py before`

The snapshot is read-only: one psycopg2 session with `readonly=True`, running SELECTs only. It never imports
`resource_explorer`. It records:

- the server time;
- the column lists of the three tables;
- the row counts of `project_code_symbols`, `project_code_markers` and `step_runs`, plus `projects` and
  `activity_log`;
- the granted advisory locks.

It exits 1 if any of the three columns already exists.

```
REGISTRY_SNAPSHOT_URL=postgresql://<user>:<pwd>@localhost:5442/egeria_advisor \
    python ~/.re-gate/scripts/ddl_artifact_columns_snapshot.py before > before.txt
```

## AFTER check: the same script, `after`

It checks the following, and exits 1 if any check fails:

- Each column exists once, in `resource_explorer`, as `text`, nullable, with no default.
- The new column is appended last. **What proves that every other column is unchanged is the `diff before.txt
  after.txt`:** both snapshots print every table's full column list, so the only difference should be the
  appended column.
- The non-NULL counts are reported: 0 right after the restart, and they rise only as surveys run. A non-NULL
  `file_role` must be one of the five roles.

Then run `diff before.txt after.txt`. The row counts should be unchanged apart from ordinary traffic.

## Creator honesty

Postgres keeps no timestamp for an ADD COLUMN. The record therefore names which process ran first after the
owner's word. Later `_init_schema` runs are no-ops, because each column is checked first and the ALTER uses
`IF NOT EXISTS`.
