# `resource-explorer schema apply` and `RE_SCHEMA_MODE` (TR-112b)

Why: `ProjectRegistry.__init__` runs about 440 DDL statements (some take ACCESS EXCLUSIVE locks) once per process per
database URL. On Kubernetes several pods starting together would contend. The schema step becomes its own Job.

* `resource-explorer schema apply` runs the setup against the configured registry database and exits 0, printing
  `schema ready · N statements · <elapsed>`. It resolves the database URL exactly as `web` does, including
  `?options=-csearch_path%3D...`; the marker lives in that schema.
* It writes a one-row marker table, `schema_version` (`schema_hash`, `statements`, `applied_at`). The hash is of the
  schema-defining code (`_init_schema` and everything it reaches; comments and docstrings do not change it).
* A second run finds the marker for this code, sends no DDL, and prints the same line (the stored count).
  A Postgres advisory lock makes concurrent runs take turns.
* `RE_SCHEMA_MODE=auto` (default) is today's behaviour: construction runs the setup. Compose is unchanged.
  `RE_SCHEMA_MODE=external`: construction never runs DDL. It checks the marker and raises `SchemaNotReady`
  ("the RE schema is not at this version: run `resource-explorer schema apply` (the chart's schema Job) first") when it is
  missing or for other code.
* Container: `docker run ... trellis/resource-explorer schema-apply` (entrypoint role `schema-apply`).
* Not covered: other components run their own DDL (feedback, metrics collector, vector store, leader election). `external`
  mode governs `ProjectRegistry` only.

Tests: `tests/test_schema_apply.py`; Postgres cases need `RE_SCHEMA_TEST_PG_URL` (a throwaway database, no search_path) and skip otherwise.
